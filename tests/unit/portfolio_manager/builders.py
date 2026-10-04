"""Record builders for the Portfolio Manager's unit tests (see support.py for the clock)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.unit.portfolio_manager.support import NOW, SOURCE
from trading_agent.portfolio_manager.inputs import (
    AccountRecord,
    DecisionRecord,
    Inputs,
    JournalRecord,
    PositionRecord,
    ReportRecord,
)


def report(
    id="r-1",
    symbol="AAPL",
    direction="buy",
    *,
    agent="research",
    size="5",
    conviction=4,
    sources=(SOURCE,),
    rationale="Earnings beat; guidance raised.",
    generated_at=NOW - timedelta(hours=1),
    expires_at=NOW + timedelta(hours=5),
    consumed=False,
) -> ReportRecord:
    return ReportRecord(
        id=id,
        agent=agent,
        symbol=symbol,
        direction=direction,
        conviction=conviction,
        suggested_size_pct=None if size is None else Decimal(size),
        sources=tuple(sources),
        rationale_md=rationale,
        generated_at=generated_at,
        expires_at=expires_at,
        consumed=consumed,
    )


def inputs(
    reports=(),
    positions=(),
    *,
    equity="100000",
    cash="50000",
    journal=(),
    earlier=(),
    account=True,
) -> Inputs:
    return Inputs(
        reports=tuple(reports),
        positions=tuple(positions),
        account=AccountRecord(Decimal(equity), Decimal(cash), NOW - timedelta(hours=1))
        if account
        else None,
        journal=tuple(journal),
        earlier_decisions=tuple(earlier),
    )


def position(symbol="AAPL", qty="100", price="190") -> PositionRecord:
    return PositionRecord(symbol, Decimal(qty), Decimal(price))


def journal_row(day="2026-09-30", summary="A quiet day.") -> JournalRecord:
    return JournalRecord(date.fromisoformat(day), Decimal("99000"), Decimal("100000"), summary)


def earlier_decision(
    symbol="AAPL", direction="buy", size="3", at=NOW - timedelta(hours=2)
) -> DecisionRecord:
    return DecisionRecord(symbol, direction, Decimal(size), at)


def fetched(symbol="AAPL", current="200", at=None, *, fetched_at=NOW):
    """A quote as the service hands it over: with the time it was fetched."""
    from tests.unit.portfolio_manager.support import quote
    from trading_agent.portfolio_manager.inputs import FetchedQuote

    return FetchedQuote(quote(symbol, current, at or fetched_at - timedelta(minutes=1)), fetched_at)
