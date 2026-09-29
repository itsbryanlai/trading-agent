"""One tick of the reference-data job (research D7-D9).

Reads the candidate symbols, fetches the ones without today's row through a
pacer, normalizes, and inserts. Fail closed per symbol: any failure writes
nothing for that symbol today, is logged, and backs off (FR-005, FR-006, FR-014).
Provider errors never end the process; only a lost database connection does
(FR-019), and that propagates to the loop in __main__.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

import psycopg
from psycopg.rows import dict_row

from trading_agent.reference import normalize as n
from trading_agent.reference.config import ReferenceConfig
from trading_agent.reference.provider import (
    KeyRejected,
    Listing,
    MarketDataProvider,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)
from trading_agent.reference.schedule import fetch_allowed, open_warning_due
from trading_agent.reference.symbols import Candidate, build_symbol_set, is_plausible_ticker
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.reference")

# A tick stops starting new symbols after this much work, so the next tick can
# pick up newly named symbols (D8, SC-003).
TICK_BUDGET_SECONDS = 50.0
# Per-symbol retry delays after the 1st, 2nd, 3rd and every later failure (D8).
BACKOFF = (
    timedelta(minutes=5),
    timedelta(minutes=10),
    timedelta(minutes=20),
    timedelta(minutes=30),
)
KEY_BACKOFF = timedelta(minutes=15)  # FR-019a


class NotAutocommit(Exception):
    """Each insert must be its own transaction (D9)."""


class ReferenceStore(Protocol):
    def read_candidates(self) -> list[Candidate]: ...

    def recorded_symbols(self, day: date) -> set[str]: ...

    def insert(self, row: n.ReferenceRow, day: date) -> None: ...


class PgReferenceStore:
    """The job's database, as ta_reference_data: the candidate view and its own table."""

    def __init__(self, conn: psycopg.Connection, *, _allow_savepoints: bool = False) -> None:
        if not conn.autocommit and not _allow_savepoints:
            raise NotAutocommit("the reference-data job needs an autocommit connection")
        self.conn = conn

    def read_candidates(self) -> list[Candidate]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT symbol, source, named_at, active_until FROM reference_candidate_symbols"
            )
            return [Candidate(**row) for row in cur.fetchall()]

    def recorded_symbols(self, day: date) -> set[str]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT symbol FROM instrument_reference WHERE trading_day = %s", (day,))
            return {row["symbol"] for row in cur.fetchall()}

    def insert(self, row: n.ReferenceRow, day: date) -> None:
        # Its own transaction (a savepoint in tests), so one failed insert can't
        # poison the connection for the next symbol.
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO instrument_reference (symbol, trading_day, security_type, "
                "exchange_mic, market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (symbol, trading_day) DO NOTHING",
                (
                    row.symbol,
                    day,
                    row.security_type,
                    row.exchange_mic,
                    row.market_cap_usd,
                    row.avg_daily_dollar_volume_usd,
                    row.share_price_usd,
                ),
            )


@dataclass
class TickReport:
    day: date | None = None
    active: bool = False
    candidates: int = 0
    recorded: int = 0
    already: int = 0
    failed: int = 0
    deferred: int = 0
    stopped: str | None = None  # "rate_limited", "key_rejected", "list_unavailable"


@dataclass
class _Retry:
    failures: int
    next_at: datetime


@dataclass
class _Memory:
    """Per-process state; losing it on restart costs only a few extra calls."""

    retries: dict[str, _Retry] = field(default_factory=dict)
    key_retry_at: datetime | None = None
    warned_on: date | None = None


class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


class ReferenceJob:
    def __init__(
        self,
        provider: MarketDataProvider,
        store: ReferenceStore,
        config: ReferenceConfig,
        *,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
        symbol_list: tuple[date, dict[str, Listing]] | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.config = config
        self.sleep = sleep
        self.monotonic = monotonic
        # Today's US symbol list, tagged with the trading day it was fetched for.
        self.symbol_list = symbol_list
        self._interval = 60.0 / config.calls_per_minute
        self._last_call: float | None = None
        self._memory = _Memory()

    # --- the tick --------------------------------------------------------------

    def tick(self, now: datetime) -> TickReport:
        report = TickReport(day=calendar.trading_day(now))
        if not fetch_allowed(now):
            return report
        report.active = True
        day = report.day
        memory = self._memory
        if memory.key_retry_at is not None and now < memory.key_retry_at:
            return report

        try:
            recorded = self.store.recorded_symbols(day)
            candidates = self.store.read_candidates()
        except psycopg.OperationalError:
            raise  # a lost connection: the loop exits for a restart (FR-019)
        except psycopg.Error as exc:
            # Anything else is retried next tick, never a crash loop (review M3).
            log.error("reference: database read failed: %s; retrying next tick", type(exc).__name__)
            report.stopped = "database_error"
            return report
        symbol_set = build_symbol_set(candidates, self.config.seed_symbols, now)
        report.candidates = len(symbol_set.ordered) + len(symbol_set.skipped)

        if open_warning_due(now, memory.warned_on):
            missing = [s for s in symbol_set.ordered if s not in recorded]
            missing += [f.symbol for f in symbol_set.skipped]
            if missing:
                shown = ", ".join(_shown(s) for s in missing)
                log.warning("reference: at open, no data for: %s", shown)
            memory.warned_on = day

        for failure in symbol_set.skipped:
            if self._due(failure.symbol, now):
                self._failed(failure, now, report)
            else:
                report.deferred += 1

        started = self.monotonic()
        try:
            listings = self._listings(day)
            for symbol in symbol_set.ordered:
                if symbol in recorded:
                    report.already += 1
                elif not self._due(symbol, now):
                    report.deferred += 1
                elif self.monotonic() - started >= TICK_BUDGET_SECONDS:
                    report.deferred += 1
                else:
                    self._fetch_isolated(symbol, listings.get(symbol), day, now, report)
        except _Stop as stop:
            report.stopped = stop.reason
            if stop.reason == "key_rejected":
                memory.key_retry_at = now + KEY_BACKOFF
                log.error(
                    "reference: market-data key rejected; skipping this run, retrying at %s",
                    memory.key_retry_at.isoformat(),
                )
            elif stop.reason == "list_unavailable":
                log.warning("reference: US symbol list unavailable; retrying next tick")
            else:
                log.warning("reference: rate limited by the provider; continuing next tick")
            done = report.recorded + report.already + report.failed + report.deferred
            report.deferred += max(0, report.candidates - done)

        if report.recorded or report.failed or report.stopped:
            log.info(
                "reference: day=%s candidates=%d recorded=%d already=%d failed=%d deferred=%d",
                day,
                report.candidates,
                report.recorded,
                report.already,
                report.failed,
                report.deferred,
            )
        return report

    # --- pieces ------------------------------------------------------------------

    def _listings(self, day: date) -> dict[str, Listing]:
        if self.symbol_list is not None and self.symbol_list[0] == day:
            return self.symbol_list[1]
        try:
            listings = self._call(self.provider.list_us_symbols)
        except ProviderUnavailable as exc:
            raise _Stop("list_unavailable") from exc
        self.symbol_list = (day, listings)
        return listings

    def _fetch_isolated(self, symbol, listing, day, now, report) -> None:
        """One symbol; an unexpected error fails that symbol only (review M3)."""
        try:
            self._fetch_one(symbol, listing, day, now, report)
        except (_Stop, psycopg.OperationalError):
            raise
        except Exception:
            log.exception("reference: %s: unexpected error", _shown(symbol))
            self._failed(n.Failure(symbol, n.INTERNAL_ERROR), now, report)

    def _fetch_one(self, symbol, listing, day, now, report) -> None:
        failure = n.listing_failure(symbol, listing)
        if failure is not None:  # costs no provider call (review M2)
            self._failed(failure, now, report)
            return
        try:
            profile = self._call(self.provider.get_profile, symbol)
            quote = self._call(self.provider.get_quote, symbol)
            metrics = self._call(self.provider.get_metrics, symbol)
        except NotPermitted:
            self._failed(n.Failure(symbol, n.NOT_PERMITTED), now, report)
            return
        except ProviderUnavailable:
            self._failed(n.Failure(symbol, n.PROVIDER_UNAVAILABLE), now, report)
            return
        result = n.normalize(symbol, listing, profile, quote, metrics, now)
        if isinstance(result, n.Failure):
            self._failed(result, now, report)
            return
        try:
            self.store.insert(result, day)
        except psycopg.OperationalError:
            raise  # a lost connection: the loop exits for a restart (FR-019)
        except psycopg.Error as exc:
            log.warning("reference: %s insert failed: %s", _shown(symbol), type(exc).__name__)
            self._failed(n.Failure(symbol, n.DATABASE_ERROR), now, report)
            return
        self._memory.retries.pop(symbol, None)
        report.recorded += 1

    def _call(self, method, *args):
        """Paced to calls_per_minute (D8). Rate limits and key rejections stop the tick."""
        if self._last_call is not None:
            wait = self._last_call + self._interval - self.monotonic()
            if wait > 0:
                self.sleep(wait)
        self._last_call = self.monotonic()
        try:
            return method(*args)
        except RateLimited as exc:
            raise _Stop("rate_limited") from exc
        except KeyRejected as exc:
            raise _Stop("key_rejected") from exc

    def _due(self, symbol: str, now: datetime) -> bool:
        retry = self._memory.retries.get(symbol)
        return retry is None or now >= retry.next_at

    def _failed(self, failure: n.Failure, now: datetime, report: TickReport) -> None:
        previous = self._memory.retries.get(failure.symbol)
        count = previous.failures + 1 if previous else 1
        next_at = now + BACKOFF[min(count, len(BACKOFF)) - 1]
        self._memory.retries[failure.symbol] = _Retry(count, next_at)
        report.failed += 1
        log.warning(
            "reference: %s failed: %s; next attempt at %s",
            _shown(failure.symbol),
            failure.reason,
            next_at.isoformat(),
        )


def _shown(symbol) -> str:
    """Symbols come from LLM-written reports: log anything that isn't a plain
    ticker as a repr, so it can't forge a log line (review LOW)."""
    return symbol if is_plausible_ticker(symbol) else repr(symbol)
