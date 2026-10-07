"""One Opportunistic Identifier run and its database access (specs/011 research O3-O12).

Window check -> open names -> slice -> fetch (paced, with a deadline; see fetch.py) -> screen
-> shortlist -> one model call -> check -> one write. Every run that reaches the database
writes at least one row (FR-016), in one transaction (FR-015). A run that writes no
proposal writes one `no_action` row naming why, with the run's counts; a failure writes one
naming its category and never the exception's text, since a provider's error body could hold
anything. Database errors are not caught here: they reach __main__ as exit 3.

The store is the agent's only database access: it reads its own still-open reports and
inserts its own `reports` rows, as `ta_opportunistic_identifier`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb

from trading_agent.llm.ports import (
    ModelClient,
    ModelError,
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
)
from trading_agent.opportunistic_identifier import answer as a
from trading_agent.opportunistic_identifier import outcome as o
from trading_agent.opportunistic_identifier import prompt as p
from trading_agent.opportunistic_identifier import rotation, screen
from trading_agent.opportunistic_identifier.answer import ReportRow
from trading_agent.opportunistic_identifier.config import OIConfig
from trading_agent.opportunistic_identifier.fetch import Fetcher
from trading_agent.opportunistic_identifier.outcome import Failed, RunOutcome
from trading_agent.opportunistic_identifier.ports import MarketData
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.opportunistic_identifier")

# A row written at or after the close would break `expires_at > generated_at`, and the write
# comes minutes after the start: leave this much room.
CLOSE_MARGIN = timedelta(minutes=1)
_MODEL_CATEGORIES = (
    (ModelKeyRejected, o.MODEL_KEY_REJECTED),
    (ModelRejected, o.MODEL_REJECTED_REQUEST),
    (ModelUnavailable, o.MODEL_UNAVAILABLE),
    (ModelRefused, o.MODEL_REFUSED),
    (ModelTruncated, o.MODEL_TRUNCATED),
)
_PLACEHOLDER = datetime.min.replace(tzinfo=UTC)  # replaced with the day's close in `_finish`


class OIStore(Protocol):
    def open_symbols(self, now: datetime) -> frozenset[str]: ...

    def write(self, rows: list[ReportRow]) -> None: ...


class NotAutocommit(Exception):
    pass


class PgOIStore:
    """The agent's database, as ta_opportunistic_identifier."""

    def __init__(self, conn: psycopg.Connection, *, _allow_savepoints: bool = False) -> None:
        if not conn.autocommit and not _allow_savepoints:
            # Each statement must commit on its own, and the write must be a real
            # transaction rather than a savepoint inside an implicit one.
            raise NotAutocommit("the Opportunistic Identifier needs an autocommit connection")
        self.conn = conn

    def open_symbols(self, now: datetime) -> frozenset[str]:
        """Symbols with a still-open buy report of this agent's own (research O6)."""
        rows = self.conn.execute(
            "SELECT symbol FROM reports WHERE agent = 'opportunistic_identifier' "
            "AND direction <> 'no_action' AND expires_at > %s",
            (now,),
        ).fetchall()
        return frozenset(row["symbol"] if isinstance(row, dict) else row[0] for row in rows)

    def write(self, rows: list[ReportRow]) -> None:
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct,"
                " sources, rationale_md, expires_at)"
                " VALUES ('opportunistic_identifier', %s, %s, %s, %s, %s, %s, %s)",
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


class OIRun:
    def __init__(
        self,
        market: MarketData,
        model: ModelClient,
        store: OIStore,
        cfg: OIConfig,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
        dry_run: bool = False,
    ) -> None:
        self.market = market
        self.model = model
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.sleep = sleep
        self.monotonic = monotonic
        self.dry_run = dry_run

    def run(self) -> RunOutcome:
        started = self.monotonic()
        now = self.clock()
        today = calendar.trading_day(now)
        if not self.dry_run and not self._in_window(now, today):
            log.info("opportunistic_identifier: outside the trading window; nothing to do")
            return RunOutcome(skipped=True)
        day = _expiry_day(today)
        open_symbols = self.store.open_symbols(now)  # a database error ends the run (exit 3)

        scan = rotation.slice_for(
            self.cfg.scan_universe, today, now, self.cfg.slots, self.cfg.slice_size
        )
        outcome = RunOutcome(slice=scan)
        outcome.counts.in_slice = len(scan.symbols)
        log.info(
            "opportunistic_identifier: run started (prompt v%s, provider %s, model %s, "
            "batch %d/%d)",
            p.PROMPT_VERSION,
            self.cfg.model.provider,
            self.cfg.model.name,
            scan.batch + 1 if scan.batches else 0,
            scan.batches,
        )
        fetcher = Fetcher(
            self.market,
            self.cfg,
            clock=self.clock,
            sleep=self.sleep,
            monotonic=self.monotonic,
            deadline=self.cfg.fetch_deadline(started),
        )
        try:
            self._produce(now, day, scan, open_symbols, fetcher, outcome)
        except Failed as failed:
            self._fail(failed.category, outcome)
        except Exception as exc:  # anything unanticipated still leaves a row
            log.error("opportunistic_identifier: %s: %s", o.INTERNAL_ERROR, type(exc).__name__)
            self._fail(o.INTERNAL_ERROR, outcome)
        self._finish(outcome, day)
        return outcome

    def _in_window(self, now: datetime, today: date) -> bool:
        """An XNYS session, the market open, and at or after the first slot (research O12).
        A 15:00 slot that starts late still runs until the close."""
        first = datetime.combine(today, self.cfg.slots.first, tzinfo=rotation.NEW_YORK)
        return calendar.is_session(today) and calendar.market_open(now) and now >= first

    # --- the steps ------------------------------------------------------------------

    def _produce(self, now, day, scan, open_symbols, fetcher, outcome: RunOutcome) -> None:
        counts = outcome.counts
        if not scan.symbols:
            return self._quiet(o.EMPTY_SCAN_UNIVERSE, outcome)
        candidates = fetcher.fetch(scan, outcome)
        short = screen.shortlist(candidates, open_symbols, self.cfg.shortlist_size)
        counts.already_open, counts.eligible = short.already_open, short.eligible
        counts.shortlisted = len(short.candidates)
        outcome.shortlist = short.candidates
        log.info(
            "opportunistic_identifier: %d in slice, %d fetched, %d skipped (%s), %d already open",
            counts.in_slice,
            counts.fetched,
            sum(counts.skipped.values()),
            _tally(counts.skipped, "none"),
            counts.already_open,
        )
        log.info(
            "opportunistic_identifier: %d eligible, %d shortlisted",
            counts.eligible,
            counts.shortlisted,
        )
        if not short.candidates:
            return self._quiet(o.EMPTY_SHORTLIST, outcome)

        user = p.build_user(now, day, short.candidates)
        system = p.system_prompt(self.cfg.rationale_max_chars)
        if len(system) + len(user) > self.cfg.max_input_chars:
            log.error("opportunistic_identifier: %s: input over the limit", o.INPUT_TOO_LARGE)
            raise Failed(o.INPUT_TOO_LARGE)
        reply = self._complete(system, user)
        counts.input_tokens, counts.output_tokens = reply.input_tokens, reply.output_tokens
        log.info(
            "opportunistic_identifier: model used %s input and %s output tokens",
            reply.input_tokens,
            reply.output_tokens,
        )
        self._check(reply.text, short.candidates, open_symbols, day, outcome)

    def _complete(self, system: str, user: str):
        try:
            return self.model.complete(system, user, a.ANSWER_SCHEMA)
        except ModelError as exc:
            category = next(
                (c for cls, c in _MODEL_CATEGORIES if isinstance(exc, cls)), o.INTERNAL_ERROR
            )
            status = f" (HTTP {exc.status})" if exc.status is not None else ""
            log.error("opportunistic_identifier: %s: %s%s", category, type(exc).__name__, status)
            raise Failed(category) from None

    def _check(self, text, shortlist, open_symbols, day, outcome: RunOutcome) -> None:
        counts = outcome.counts
        checked = a.check(
            text, [c.symbol for c in shortlist], open_symbols, self.cfg.rationale_max_chars
        )
        if checked.unusable:
            log.error("opportunistic_identifier: %s: answer not usable", o.UNUSABLE_ANSWER)
            raise Failed(o.UNUSABLE_ANSWER)
        counts.proposed = checked.received
        for drop in checked.drops:
            counts.dropped[drop.reason] = counts.dropped.get(drop.reason, 0) + 1
        outcome.drops = checked.drops
        log.info(
            "opportunistic_identifier: %d proposals received, %d accepted, %d dropped",
            checked.received,
            len(checked.reports),
            len(checked.drops),
        )
        for drop in checked.drops:
            log.info(
                "opportunistic_identifier: dropped proposal %d (%s): %s",
                drop.index,
                drop.symbol or "-",
                drop.reason,
            )
        if checked.reports:
            outcome.rows = a.rows(checked, {c.symbol: c.data for c in shortlist}, day)
            counts.written = len(outcome.rows)
        else:
            self._quiet(o.ALL_DROPPED if checked.drops else o.NOTHING_ARGUED, outcome)

    # --- rows and the write ---------------------------------------------------------

    def _quiet(self, why: str, outcome: RunOutcome) -> None:
        outcome.note = why
        outcome.rows = [self._no_action(why, outcome)]

    def _fail(self, category: str, outcome: RunOutcome) -> None:
        outcome.failure = category
        outcome.rows = [self._no_action(category, outcome, failed=True)]

    def _no_action(self, why: str, outcome: RunOutcome, *, failed: bool = False) -> ReportRow:
        c = outcome.counts
        lead = "Opportunistic Identifier run failed" if failed else "Opportunistic Identifier run"
        text = (
            f"{lead}: {why}. {o.SENTENCES.get(why, '')} "
            f"{c.in_slice} in slice, {c.fetched} fetched, skipped ({_tally(c.skipped, 'none')}), "
            f"{c.already_open} already open, {c.eligible} eligible, {c.shortlisted} shortlisted; "
            f"{c.proposed} proposed, dropped ({_tally(c.dropped, 'none')})."
        )
        return ReportRow(
            None, "no_action", None, None, [], text[: self.cfg.rationale_max_chars], _PLACEHOLDER
        )

    def _finish(self, outcome: RunOutcome, day: date) -> None:
        expires_at = calendar.close_time(day)
        if not self.dry_run and self.clock() >= expires_at - CLOSE_MARGIN:
            # The run outlasted its window: a row written now would expire before it was
            # generated. Nothing is written; the exit status says so (exit 5).
            log.error(
                "opportunistic_identifier: %s: the close passed before the write; nothing written",
                o.WINDOW_CLOSED,
            )
            outcome.rows, outcome.failure = [], o.WINDOW_CLOSED
            return
        outcome.rows = [replace(row, expires_at=expires_at) for row in outcome.rows]
        if not self.dry_run:
            self.store.write(outcome.rows)  # not caught: a database error is exit 3
        if outcome.failure is None and outcome.rows[0].direction != "no_action":
            log.info("opportunistic_identifier: wrote %d report(s)", len(outcome.rows))
        else:
            log.info(
                "opportunistic_identifier: wrote no_action (%s)", outcome.failure or outcome.note
            )


def _tally(counts: dict[str, int], empty: str) -> str:
    return ", ".join(f"{reason}: {n}" for reason, n in sorted(counts.items())) or empty


def _expiry_day(today: date) -> date:
    """Today's session, or the next one when `today` isn't (only a dry run gets there)."""
    day = today
    while not calendar.is_session(day):
        day += timedelta(days=1)
    return day
