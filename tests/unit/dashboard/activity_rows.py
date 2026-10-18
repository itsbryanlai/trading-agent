"""Activity rows as the store returns them, for the view and route tests (T022)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

AT = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)

SOURCE = {
    "title": "Q3 earnings beat",
    "url": "https://news.example/a?b=1&c=2",
    "publisher": "Example Wire",
    "published_at": "2026-10-18T12:00:00Z",
    "relevance": "high",
}


def report(id="r-1", at=AT, **changes) -> dict:
    return {
        "id": id,
        "agent": "research",
        "generated_at": at,
        "symbol": "AAPL",
        "direction": "buy",
        "conviction": 4,
        "suggested_size_pct": Decimal("5.000"),
        "sources": [SOURCE],
        "rationale_md": "**Strong** quarter.",
        **changes,
    }


def no_action(id="r-0", at=AT, **changes) -> dict:
    return report(
        id,
        at,
        agent="opportunistic_identifier",
        symbol=None,
        direction="no_action",
        conviction=None,
        suggested_size_pct=None,
        sources=[],
        rationale_md="Nothing stands out.",
        **changes,
    )


def decision(id="d-1", at=AT, report_ids=("r-1",), **changes) -> dict:
    return {
        "id": id,
        "generated_at": at,
        "symbol": "AAPL",
        "direction": "buy",
        "size_pct": Decimal("5.000"),
        "reasoning_md": "Both analysts agree.",
        "quote_at_decision": Decimal("187.2500"),
        "quote_time": at,
        "report_ids": list(report_ids),
        **changes,
    }


def run(id="u-1", at=AT, **changes) -> dict:
    return {
        "id": id,
        "agent": "research",
        "trading_day": at.date(),
        "reason": "scheduled",
        "slot_key": "research_daily",
        "slot_at": at,
        "started_at": at,
        "finished_at": at,
        "outcome": "succeeded",
        "detail": None,
        **changes,
    }


def timed_out(id="u-2", at=AT, **changes) -> dict:
    return run(id, at, **{"outcome": "timed_out", "detail": "exit 124", **changes})


def skipped(id="u-3", at=AT, **changes) -> dict:
    return run(
        id,
        at,
        **{
            "agent": "portfolio_manager",
            "reason": "morning_session",
            "slot_key": "morning_session",
            "started_at": None,
            "finished_at": None,
            "outcome": "skipped",
            "detail": "trading paused",
            **changes,
        },
    )
