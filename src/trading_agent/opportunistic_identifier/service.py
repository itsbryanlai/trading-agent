"""One Opportunistic Identifier run and its database access (specs/011 research O3-O12).

Open names -> slice -> symbol list -> per-name fetch (paced, with a deadline) -> screen ->
shortlist -> one model call -> check -> one write. Every run that reaches the database
writes at least one row (FR-016), in one transaction (FR-015). A run that writes no
proposal writes one `no_action` row naming why, with the run's counts. Database errors are
not caught here: they reach __main__ as exit 3.

The store is the agent's only database access: it reads its own still-open reports and
inserts its own `reports` rows, as `ta_opportunistic_identifier`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb

from trading_agent.llm.ports import ModelClient
from trading_agent.opportunistic_identifier import answer as a
from trading_agent.opportunistic_identifier import prompt as p
from trading_agent.opportunistic_identifier import rotation, screen
from trading_agent.opportunistic_identifier.answer import ReportRow
from trading_agent.opportunistic_identifier.config import OIConfig
from trading_agent.opportunistic_identifier.ports import (
    Listing,
    MarketData,
    NotPermitted,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from trading_agent.opportunistic_identifier.screen import Skip
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.opportunistic_identifier")

# Skip reasons that come from the fetch itself (the contract's closed set).
NOT_FETCHED = "not_fetched"
PROVIDER_UNAVAILABLE = "provider_unavailable"
NOT_PERMITTED = "not_permitted"
RATE_LIMITED = "rate_limited"

# Quiet no_action reasons (exit 0) and the one failure this phase writes.
EMPTY_SCAN_UNIVERSE = "empty_scan_universe"
EMPTY_SHORTLIST = "empty_shortlist"
NOTHING_ARGUED = "nothing_argued"
ALL_DROPPED = "all_dropped"
INPUT_TOO_LARGE = "input_too_large"

_SENTENCES = {
    EMPTY_SCAN_UNIVERSE: "The scan list is empty.",
    EMPTY_SHORTLIST: "No name in the slice was eligible and not already open.",
    NOTHING_ARGUED: "The model proposed nothing worth arguing.",
    ALL_DROPPED: "Every proposal the model made was dropped by the checks.",
    INPUT_TOO_LARGE: "The model input was over the size limit, so no call was made.",
}
_FETCH_ERRORS = (
    (NotPermitted, NOT_PERMITTED),
    (RateLimited, RATE_LIMITED),
    (ProviderUnavailable, PROVIDER_UNAVAILABLE),
)


@dataclass
class RunCounts:
    in_slice: int = 0
    fetched: int = 0  # names whose quote was obtained
    skipped: dict[str, int] = field(default_factory=dict)
    already_open: int = 0
    eligible: int = 0
    shortlisted: int = 0
    proposed: int = 0
    written: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class RunOutcome:
    rows: list[ReportRow] = field(default_factory=list)
    counts: RunCounts = field(default_factory=RunCounts)
    slice: rotation.ScanSlice | None = None
    skips: list[Skip] = field(default_factory=list)
    shortlist: tuple[screen.Candidate, ...] = ()
    drops: tuple[a.Drop, ...] = ()
    failure: str | None = None  # a failure category: a failure no_action was written
    note: str = ""  # why a quiet no_action was written


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


class _Failed(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class _OutOfTime(Exception):
    """The fetch deadline passed: no further call starts."""


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
        self._interval = 60 / cfg.finnhub_calls_per_minute
        self._deadline = 0.0
        self._last_weight: int | None = None  # None until the first Finnhub call

    def run(self) -> RunOutcome:
        started = self.monotonic()
        self._deadline = self.cfg.fetch_deadline(started)
        now = self.clock()
        today = calendar.trading_day(now)
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
        try:
            self._produce(now, day, scan, open_symbols, outcome)
        except _Failed as failed:
            outcome.failure = failed.category
            outcome.rows = [self._no_action(failed.category, outcome, failed=True)]
        self._finish(outcome, day)
        return outcome

    # --- the steps ------------------------------------------------------------------

    def _produce(self, now, day, scan, open_symbols, outcome: RunOutcome) -> None:
        counts = outcome.counts
        if not scan.symbols:
            return self._quiet(EMPTY_SCAN_UNIVERSE, outcome)
        candidates = self._fetch(scan, outcome)
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
            return self._quiet(EMPTY_SHORTLIST, outcome)

        user = p.build_user(now, day, short.candidates)
        if len(p.SYSTEM_PROMPT) + len(user) > self.cfg.max_input_chars:
            log.error("opportunistic_identifier: %s: input over the size limit", INPUT_TOO_LARGE)
            raise _Failed(INPUT_TOO_LARGE)
        reply = self.model.complete(p.SYSTEM_PROMPT, user, a.ANSWER_SCHEMA)
        counts.input_tokens, counts.output_tokens = reply.input_tokens, reply.output_tokens
        log.info(
            "opportunistic_identifier: model used %s input and %s output tokens",
            reply.input_tokens,
            reply.output_tokens,
        )

        checked = a.check(
            reply.text,
            [c.symbol for c in short.candidates],
            open_symbols,
            self.cfg.rationale_max_chars,
        )
        if checked.unusable:
            log.error("opportunistic_identifier: unusable_answer: not in the required shape")
            raise _Failed("unusable_answer")
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
            data = {c.symbol: c.data for c in short.candidates}
            outcome.rows = a.rows(checked, data, day)
            counts.written = len(outcome.rows)
        else:
            self._quiet(ALL_DROPPED if checked.drops else NOTHING_ARGUED, outcome)

    def _fetch(self, scan: rotation.ScanSlice, outcome: RunOutcome) -> list[screen.Candidate]:
        """The symbol list once, then each name's quote, profile and fundamentals, paced.
        Names that fail the listing check cost no call. Partial data never fails the run."""
        listings: dict[str, Listing] = self._paced(self.market.us_listings, weight=3)
        candidates: list[screen.Candidate] = []
        for symbol in scan.symbols:
            result = self._name(symbol, listings.get(symbol), outcome)
            if isinstance(result, screen.Candidate):
                candidates.append(result)
            else:
                self._skip(outcome, result)
        return candidates

    def _name(self, symbol: str, listing: Listing | None, outcome: RunOutcome):
        stop = screen.listing_stop(symbol, listing)
        if stop is not None:
            return stop
        try:
            quote = self._paced(lambda: self.market.quote(symbol))
            outcome.counts.fetched += 1
            fetched_at = self.clock()
            if screen.is_stale(
                quote, fetched_at, timedelta(minutes=self.cfg.quote_max_age_minutes)
            ):
                return Skip(symbol, screen.STALE_QUOTE)  # a stale name costs one call, not three
            profile = self._paced(lambda: self.market.profile(symbol))
            fundamentals = self._paced(lambda: self.market.fundamentals(symbol))
        except _OutOfTime:
            return Skip(symbol, NOT_FETCHED)
        except ProviderError as exc:
            return Skip(symbol, _fetch_reason(exc))
        return screen.assess(
            symbol,
            listing,
            profile,
            quote,
            fundamentals,
            fetched_at,
            self.cfg.universe,
            timedelta(minutes=self.cfg.quote_max_age_minutes),
        )

    def _paced(self, call, *, weight: int = 1):
        """Space Finnhub calls by the configured pace. The symbol list is three requests, so
        the call after it waits three intervals. No call starts after the fetch deadline."""
        wait = 0.0 if self._last_weight is None else self._interval * self._last_weight
        if self.monotonic() + wait >= self._deadline:
            raise _OutOfTime  # without sleeping: the call would start after the deadline
        if wait:
            self.sleep(wait)
        self._last_weight = weight
        return call()

    # --- rows and the write ---------------------------------------------------------

    def _skip(self, outcome: RunOutcome, skip: Skip) -> None:
        outcome.skips.append(skip)
        counts = outcome.counts.skipped
        counts[skip.reason] = counts.get(skip.reason, 0) + 1

    def _quiet(self, why: str, outcome: RunOutcome) -> None:
        outcome.note = why
        outcome.rows = [self._no_action(why, outcome)]

    def _no_action(self, why: str, outcome: RunOutcome, *, failed: bool = False) -> ReportRow:
        c = outcome.counts
        lead = "Opportunistic Identifier run failed" if failed else "Opportunistic Identifier run"
        text = (
            f"{lead}: {why}. {_SENTENCES.get(why, '')} "
            f"{c.in_slice} in slice, {c.fetched} fetched, skipped ({_tally(c.skipped, 'none')}), "
            f"{c.already_open} already open, {c.eligible} eligible, {c.shortlisted} shortlisted; "
            f"{c.proposed} proposed, dropped ({_tally(c.dropped, 'none')})."
        )
        return ReportRow(
            None, "no_action", None, None, [], text[: self.cfg.rationale_max_chars], _PLACEHOLDER
        )

    def _finish(self, outcome: RunOutcome, day: date) -> None:
        expires_at = calendar.close_time(day)
        outcome.rows = [replace(row, expires_at=expires_at) for row in outcome.rows]
        if not self.dry_run:
            self.store.write(outcome.rows)  # not caught: a database error is exit 3
        if outcome.failure is None and outcome.rows[0].direction != "no_action":
            log.info("opportunistic_identifier: wrote %d report(s)", len(outcome.rows))
        else:
            log.info(
                "opportunistic_identifier: wrote no_action (%s)", outcome.failure or outcome.note
            )


_PLACEHOLDER = datetime.min.replace(tzinfo=UTC)  # replaced with the day's close in `_finish`


def _fetch_reason(exc: ProviderError) -> str:
    for cls, reason in _FETCH_ERRORS:
        if isinstance(exc, cls):
            return reason
    raise exc  # KeyRejected: a failure, handled with the failure categories


def _tally(counts: dict[str, int], empty: str) -> str:
    return ", ".join(f"{reason}: {n}" for reason, n in sorted(counts.items())) or empty


def _expiry_day(today: date) -> date:
    """Today's session, or the next one when `today` isn't (only a dry run gets there)."""
    day = today
    while not calendar.is_session(day):
        day += timedelta(days=1)
    return day
