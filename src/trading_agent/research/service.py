"""One Research run (specs/007-research-agent research R1, R3, R8).

Window check → open reports → symbol list → news (paced, with a deadline) →
selection → prompt → one model call → check → one write. Every run that reaches
the database writes at least one row (FR-011), in one transaction (FR-012). A
failure becomes a `no_action` row naming its category and never the exception's
text, since a provider's error body could hold anything. Database errors are not
caught here: they reach __main__ as exit 3.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb

from trading_agent.research import answer as a
from trading_agent.research import prompt as p
from trading_agent.research.config import ResearchConfig
from trading_agent.research.ports import (
    KeyRejected,
    ModelClient,
    ModelError,
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
    NewsError,
    NewsSource,
    RawArticle,
)
from trading_agent.research.selection import select
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.research")

CLOSE_MARGIN = timedelta(minutes=1)

# Failure categories (a closed set; contracts/research-interface.md).
NEWS_UNAVAILABLE = "news_unavailable"
SYMBOL_LIST_UNAVAILABLE = "symbol_list_unavailable"
MODEL_KEY_REJECTED = "model_key_rejected"
MODEL_REJECTED_REQUEST = "model_rejected_request"
MODEL_UNAVAILABLE = "model_unavailable"
MODEL_REFUSED = "model_refused"
MODEL_TRUNCATED = "model_truncated"
UNUSABLE_ANSWER = "unusable_answer"
INTERNAL_ERROR = "internal_error"
# Not a row category: the run ran out of window before writing, so nothing is written.
WINDOW_CLOSED = "window_closed"

_FAILURE_SENTENCES = {
    NEWS_UNAVAILABLE: "No news could be fetched, or the news key was rejected.",
    SYMBOL_LIST_UNAVAILABLE: "The list of US-listed symbols could not be fetched.",
    MODEL_KEY_REJECTED: "The model provider rejected the key.",
    MODEL_REJECTED_REQUEST: "The model provider rejected the request.",
    MODEL_UNAVAILABLE: "The model provider could not be reached or did not answer in time.",
    MODEL_REFUSED: "The model declined to answer.",
    MODEL_TRUNCATED: "The model's answer hit the output limit and was discarded.",
    UNUSABLE_ANSWER: "The model's answer was not in the required shape.",
    INTERNAL_ERROR: "Research hit an unexpected error.",
}
_MODEL_CATEGORIES = (
    (ModelKeyRejected, MODEL_KEY_REJECTED),
    (ModelRejected, MODEL_REJECTED_REQUEST),
    (ModelUnavailable, MODEL_UNAVAILABLE),
    (ModelRefused, MODEL_REFUSED),
    (ModelTruncated, MODEL_TRUNCATED),
)

NOTHING_TO_ARGUE = "Nothing worth arguing in today's news."
NO_NEWS = "No news in the window."


@dataclass(frozen=True)
class ReportRow:
    symbol: str | None
    direction: str
    conviction: int | None
    suggested_size_pct: Decimal | None
    sources: list[dict]
    rationale_md: str
    expires_at: datetime


@dataclass
class RunOutcome:
    rows: list[ReportRow] = field(default_factory=list)
    drops: tuple[a.Drop, ...] = ()
    missing: list[str] = field(default_factory=list)
    failure: str | None = None
    skipped: bool = False  # outside the trading window: nothing fetched or written
    input_tokens: int | None = None
    output_tokens: int | None = None
    input_chars: int = 0
    tagged_articles: int = 0  # sent articles carrying at least one ticker tag
    articles: tuple = ()  # the articles sent to the model (the dry run prints them)
    note: str = ""  # why a non-failure no_action was written


class ResearchStore(Protocol):
    def open_reports(self, now: datetime) -> list[tuple[str, str]]: ...

    def write(self, rows: list[ReportRow]) -> None: ...


class NotAutocommit(Exception):
    pass


class PgResearchStore:
    """Research's database, as ta_research: its own reports, read and inserted."""

    def __init__(self, conn: psycopg.Connection, *, _allow_savepoints: bool = False) -> None:
        if not conn.autocommit and not _allow_savepoints:
            # Each statement must commit on its own, and the write must be a real
            # transaction rather than a savepoint inside an implicit one.
            raise NotAutocommit("Research needs an autocommit connection")
        self.conn = conn

    def open_reports(self, now: datetime) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT symbol, direction FROM reports WHERE agent = 'research' "
            "AND direction <> 'no_action' AND expires_at > %s",
            (now,),
        ).fetchall()
        return [_pair(row) for row in rows]

    def write(self, rows: list[ReportRow]) -> None:
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct,"
                " sources, rationale_md, expires_at)"
                " VALUES ('research', %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        r.symbol,
                        r.direction,
                        r.conviction,
                        r.suggested_size_pct,
                        Jsonb(r.sources),
                        r.rationale_md,
                        r.expires_at,
                    )
                    for r in rows
                ],
            )


def _pair(row) -> tuple[str, str]:
    if isinstance(row, dict):
        return (row["symbol"], row["direction"])
    return (row[0], row[1])


class _Failed(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class ResearchRun:
    def __init__(
        self,
        news: NewsSource,
        model: ModelClient,
        store: ResearchStore,
        cfg: ResearchConfig,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
        dry_run: bool = False,
    ) -> None:
        self.news = news
        self.model = model
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.sleep = sleep
        self.monotonic = monotonic
        self.dry_run = dry_run

    def run(self) -> RunOutcome:
        now = self.clock()
        today = calendar.trading_day(now)
        in_window = calendar.is_session(today) and now < calendar.close_time(today) - CLOSE_MARGIN
        if not in_window and not self.dry_run:
            log.info("research: not a trading session before the close; nothing to do")
            return RunOutcome(skipped=True)
        expires_at = calendar.close_time(today if in_window else _next_session(today))

        log.info(
            "research: run started (prompt v%s, provider %s, model %s)",
            p.PROMPT_VERSION,
            self.cfg.model.provider,
            self.cfg.model.name,
        )
        open_reports = self.store.open_reports(now)  # a database error ends the run (exit 3)

        outcome = RunOutcome()
        try:
            self._produce(now, today, open_reports, outcome)
        except _Failed as failed:
            outcome.failure = failed.category
        except Exception as exc:  # anything unanticipated still leaves a row (analyze G2)
            log.error("research: %s: %s", INTERNAL_ERROR, type(exc).__name__)
            outcome.failure = INTERNAL_ERROR

        if outcome.failure is not None:
            outcome.rows = [
                _no_action(
                    f"Research run failed: {outcome.failure}. {_FAILURE_SENTENCES[outcome.failure]}"
                )
            ]
        outcome.rows = [
            replace(
                row,
                expires_at=expires_at,
                rationale_md=_with_missing(row, outcome.missing, self.cfg.rationale_max_chars),
            )
            for row in outcome.rows
        ]
        if not self.dry_run and self.clock() >= expires_at - CLOSE_MARGIN:
            # Review M1: the run can outlast the window it started in (the write comes
            # minutes after the start), and a row written at or after the close would
            # break `expires_at > generated_at`. Nothing is written; exit 1.
            log.error(
                "research: %s: the close passed before the write; nothing written", WINDOW_CLOSED
            )
            outcome.rows, outcome.failure = [], WINDOW_CLOSED
            return outcome
        self.store.write(outcome.rows)  # not caught: a database error is exit 3
        if outcome.failure is None and outcome.rows[0].direction != "no_action":
            log.info("research: wrote %d report(s)", len(outcome.rows))
        else:
            log.info("research: wrote no_action (%s)", outcome.failure or outcome.note)
        return outcome

    # --- the steps ------------------------------------------------------------------

    def _produce(self, now: datetime, today: date, open_reports, outcome: RunOutcome) -> None:
        symbols, general, by_symbol = self._fetch(now, today, outcome)

        chosen = select(general, by_symbol, now, self.cfg)
        built = p.build(chosen.articles, open_reports, today, self.cfg.max_input_chars)
        outcome.input_chars = built.input_chars
        outcome.articles = built.articles
        # Tagged articles are the ones citable for a symbol without naming it (secondary
        # relevance), so this helps tell "nothing to cite" from "nothing interesting".
        outcome.tagged_articles = sum(1 for art in built.articles if art.related)
        log.info(
            "research: %d articles in window, %d sent (%d tagged with a ticker; %d chars, "
            "%d dropped for size); missing: %s",
            chosen.in_window,
            len(built.articles),
            outcome.tagged_articles,
            built.input_chars,
            built.dropped_for_size,
            _missing_text(outcome.missing) or "none",
        )
        if not built.articles:
            outcome.rows = [_no_action(NO_NEWS)]
            outcome.note = "no news"
            return

        try:
            reply = self.model.complete(built.system, built.user, a.ANSWER_SCHEMA)
        except ModelError as exc:
            category = next(c for cls, c in _MODEL_CATEGORIES if isinstance(exc, cls))
            status = f" (HTTP {exc.status})" if exc.status is not None else ""
            log.error("research: %s: %s%s", category, type(exc).__name__, status)
            raise _Failed(category) from None
        outcome.input_tokens, outcome.output_tokens = reply.input_tokens, reply.output_tokens
        log.info(
            "research: model used %s input and %s output tokens",
            reply.input_tokens,
            reply.output_tokens,
        )

        checked = a.check(
            reply.text,
            {art.id: art for art in built.articles},
            symbols,
            open_reports,
            self.cfg.rationale_max_chars,
        )
        if checked.unusable:
            log.error("research: %s: answer not in the required shape", UNUSABLE_ANSWER)
            raise _Failed(UNUSABLE_ANSWER)
        outcome.drops = checked.drops
        log.info(
            "research: %d proposals received, %d accepted, %d dropped",
            checked.received,
            len(checked.reports),
            len(checked.drops),
        )
        for drop in checked.drops:
            log.info(
                "research: dropped proposal %d (%s): %s",
                drop.index,
                drop.symbol or "-",
                drop.reason,
            )
        if checked.reports:
            outcome.rows = [
                ReportRow(
                    symbol=r.symbol,
                    direction=r.direction,
                    conviction=r.conviction,
                    suggested_size_pct=r.size,
                    sources=list(r.sources),
                    rationale_md=r.rationale,
                    expires_at=now,  # replaced in run()
                )
                for r in checked.reports
            ]
        elif checked.drops:
            outcome.rows = [_no_action(_all_dropped(checked.drops))]
            outcome.note = "all proposals dropped"
        else:
            outcome.rows = [_no_action(NOTHING_TO_ARGUE)]
            outcome.note = "nothing to argue"

    def _fetch(self, now: datetime, today: date, outcome: RunOutcome):
        """The symbol list, then general news, then each watchlist symbol's company
        news: paced, and stopped at the news deadline (research R3)."""
        start_mono = self.monotonic()
        deadline = start_mono + self.cfg.news_budget_seconds
        interval = 60 / self.cfg.finnhub_calls_per_minute
        calls = 0

        def paced(call):
            nonlocal calls
            if calls:
                self.sleep(interval)
            calls += 1
            return call()

        try:
            symbols = paced(self.news.us_symbols)
        except KeyRejected:
            log.error("research: %s: KeyRejected", NEWS_UNAVAILABLE)
            raise _Failed(NEWS_UNAVAILABLE) from None
        except NewsError as exc:
            log.error("research: %s: %s", SYMBOL_LIST_UNAVAILABLE, type(exc).__name__)
            raise _Failed(SYMBOL_LIST_UNAVAILABLE) from None

        window_from = calendar.previous_session(today)  # ET dates; selection trims to the minute
        feeds: list[str | None] = [None, *self.cfg.watchlist]
        general: list[RawArticle] = []
        by_symbol: dict[str, list[RawArticle]] = {}
        failed = 0
        for feed in feeds:
            name = feed or "general"
            if self.monotonic() >= deadline:
                outcome.missing.append(name)
                failed += 1  # not fetched counts as failed (review L2, converge T049)
                continue
            try:
                if feed is None:
                    general = paced(self.news.general_news)
                else:
                    by_symbol[feed] = paced(
                        lambda f=feed: self.news.company_news(f, window_from, today)
                    )
            except KeyRejected:
                log.error("research: %s: KeyRejected", NEWS_UNAVAILABLE)
                raise _Failed(NEWS_UNAVAILABLE) from None
            except NewsError as exc:
                log.warning("research: news for %s missing: %s", name, type(exc).__name__)
                outcome.missing.append(name)
                failed += 1
        if failed == len(feeds):
            log.error("research: %s: every news fetch failed", NEWS_UNAVAILABLE)
            raise _Failed(NEWS_UNAVAILABLE)
        return symbols, general, by_symbol


def _no_action(rationale: str) -> ReportRow:
    return ReportRow(None, "no_action", None, None, [], rationale, datetime.min)


def _all_dropped(drops) -> str:
    counts = {reason: 0 for reason in a.DROP_REASONS}
    for drop in drops:
        counts[drop.reason] += 1
    detail = ", ".join(f"{reason}: {n}" for reason, n in counts.items() if n)
    return f"Nothing written: {len(drops)} proposals dropped ({detail})."


def _missing_text(missing: list[str]) -> str:
    general = ["general"] if "general" in missing else []
    symbols = [m for m in missing if m != "general"]
    parts = [*general, *([", ".join(symbols)] if symbols else [])]
    return "; ".join(parts)


def _with_missing(row: ReportRow, missing: list[str], cap: int) -> str:
    """Every row names the missing news, failure rows included (research R8). The whole
    rationale, that line included, stays within `rationale_max_chars` (review L1): the
    body is shortened first, so the Missing line survives whenever it fits."""
    body = row.rationale_md
    suffix = f"\n\nMissing news: {_missing_text(missing)}." if missing else ""
    if len(body) + len(suffix) <= cap:
        return body + suffix
    if suffix and len(suffix) < cap - len(a.ELLIPSIS):
        return a._cap(body, cap - len(suffix)) + suffix
    return a._cap(body + suffix, cap)


def _next_session(day: date) -> date:
    candidate = day + timedelta(days=1)
    while not calendar.is_session(candidate):
        candidate += timedelta(days=1)
    return candidate
