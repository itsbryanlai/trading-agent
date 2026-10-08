"""One journal run: gate, read, price, advance every book, write one row (research J3-J8, J11).

The store does the database work and the provider the quotes; this module sequences them and
holds no I/O of its own. Time is an argument.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol

from trading_agent.journal import books, facts, summary
from trading_agent.journal.config import JournalConfig
from trading_agent.journal.model import (
    Book,
    BookResult,
    JournalReads,
    JournalRow,
    ReportRow,
    RunOutcome,
)
from trading_agent.journal.prices import fetch_closes
from trading_agent.journal.state import decode_books, encode
from trading_agent.journal.usage import usage
from trading_agent.reference.provider import MarketDataProvider
from trading_agent.risk import calendar


class Store(Protocol):
    def read(
        self,
        day: date,
        open_at: datetime,
        close_at: datetime,
        previous_close_of: Callable[[date], datetime],
    ) -> JournalReads: ...

    def upsert(self, row: JournalRow) -> None: ...


def run(
    store: Store,
    market: MarketDataProvider,
    cfg: JournalConfig,
    *,
    now: datetime,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> RunOutcome:
    day = calendar.trading_day(now)
    if not calendar.is_session(day):
        return RunOutcome("nothing_to_do", "not_a_session")
    if now < calendar.close_time(day):
        return RunOutcome("nothing_to_do", "before_close")

    reads = store.read(day, calendar.open_time(day), calendar.close_time(day), calendar.close_time)
    previous_day = None if reads.previous is None else reads.previous["trading_day"]
    previous_books = (
        {} if reads.previous is None else decode_books(reads.previous["per_agent_attribution"])
    )
    reports = [_report_row(r, day) for r in reads.reports]

    held = {s for b in previous_books.values() for s in b.holdings}
    targets = {r.symbol for r in reports if r.direction in ("buy", "hold")}
    symbols = sorted(held | targets)
    prices = (
        fetch_closes(market, symbols, day, cfg, sleep=sleep, monotonic=monotonic) if symbols else {}
    )

    results = _advance_all(previous_books, reports, prices, day, cfg.holding_sessions)
    missed = _missed_sessions(previous_day, day)
    equity_open = reads.snapshot_open["equity"]
    equity_close = reads.snapshot_close["equity"]
    base_name, base = (
        ("open", equity_open)
        if reads.previous is None
        else ("previous_close", reads.previous["equity_close"])
    )
    attribution = encode(
        day=day,
        previous_day=previous_day,
        sessions_covered=len(missed) + 1,
        missed_sessions=missed,
        holding_sessions=cfg.holding_sessions,
        summary_version=summary.SUMMARY_VERSION,
        account_return=None if base == 0 else equity_close / base - 1,
        account_base=base_name,
        close_taken_at=reads.snapshot_close["taken_at"].astimezone(UTC),
        results=results,
        usage=usage(reads.reports, reads.decision_reports, reads.verdicts, reads.orders),
    )
    unpriced = sum(1 for p in prices.values() if p.close is None)
    text = summary.render(facts.day_facts(reads, day, missed, unpriced))
    row = JournalRow(day, equity_open, equity_close, text, attribution)
    store.upsert(row)
    return RunOutcome("wrote", None, row)


def _advance_all(
    previous_books: dict[str, Book],
    reports: list[ReportRow],
    prices: dict[str, Any],
    day: date,
    holding_sessions: int,
) -> dict[str, BookResult]:
    """A book for every agent in the previous row and every agent with a report today."""
    agents = sorted(set(previous_books) | {r.agent for r in reports})
    return {
        agent: books.advance(
            previous_books.get(agent) or books.new_book(agent, day),
            [r for r in reports if r.agent == agent],
            prices,
            day,
            holding_sessions,
            calendar.previous_session,
        )
        for agent in agents
    }


def _report_row(row: dict[str, Any], day: date) -> ReportRow:
    support = _support_session(row["generated_at"], day)
    size = row["suggested_size_pct"]
    return ReportRow(
        id=row["id"],
        agent=row["agent"],
        generated_at=row["generated_at"],
        symbol=row["symbol"] or "",
        direction=row["direction"],
        suggested_size_pct=None if size is None else Decimal(size),
        support_session=support,
        late=support < day,
    )


def _support_session(generated_at: datetime, day: date) -> date:
    """The first session whose close is at or after the report (research J4). Stepping back
    from `day` stops at the session whose close is before the report."""
    session = day
    while True:
        earlier = calendar.previous_session(session)
        if calendar.close_time(earlier) < generated_at:
            return session
        session = earlier


def _missed_sessions(previous_day: date | None, day: date) -> tuple[date, ...]:
    """Sessions after the previous row and before today, oldest first."""
    if previous_day is None:
        return ()
    missed = []
    session = calendar.previous_session(day)
    while session > previous_day:
        missed.append(session)
        session = calendar.previous_session(session)
    return tuple(reversed(missed))
