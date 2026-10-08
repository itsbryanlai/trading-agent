"""Row builders shared by the summary tests: `JournalReads` for one day (feature 012)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count
from typing import Any

from trading_agent.journal.model import JournalReads

DAY = date(2026, 10, 9)
NOON = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)
_ids = count(1)


def decision(symbol: str, direction: str = "buy", at: datetime = NOON, **extra: Any) -> dict:
    return {
        "id": f"d{next(_ids)}",
        "generated_at": at,
        "symbol": symbol,
        "direction": direction,
        **extra,
    }


def verdict(
    decision_id: str | None,
    kind: str = "approved",
    rule: str | None = None,
    trigger: str | None = None,
    day: date = DAY,
) -> dict:
    return {
        "id": f"v{next(_ids)}",
        "decision_id": decision_id,
        "stop_loss_trigger_id": trigger,
        "trading_day": day,
        "verdict": kind,
        "rejection_rule": rule,
    }


def order(verdict_id: str, status: str = "filled", qty: str = "1", **extra: Any) -> dict:
    return {"risk_verdict_id": verdict_id, "status": status, "fill_qty": Decimal(qty), **extra}


def reads(**over: Any) -> JournalReads:
    fields: dict[str, Any] = dict(
        previous=None,
        has_future_row=False,
        window_start=None,
        reports=[],
        decision_reports=[],
        decisions=[],
        verdicts=[],
        orders=[],
        refusals=[],
        triggers=[],
        snapshot_open={
            "taken_at": datetime(2026, 10, 9, 13, 30, tzinfo=UTC),
            "equity": Decimal("100000.00"),
        },
        snapshot_close={
            "taken_at": datetime(2026, 10, 9, 19, 30, tzinfo=UTC),
            "equity": Decimal("100000.00"),
        },
    )
    return JournalReads(**{**fields, **over})


def example_day() -> JournalReads:
    """The contract's example: AAPL bought, MSFT and NVDA rejected."""
    aapl = decision("AAPL", "buy")
    msft = decision("MSFT", "buy")
    nvda = decision("NVDA", "sell")
    ok = verdict(aapl["id"])
    return reads(
        decisions=[aapl, msft, nvda],
        verdicts=[
            ok,
            verdict(msft["id"], "rejected", "max_position_pct"),
            verdict(nvda["id"], "rejected", "trading_paused"),
        ],
        orders=[order(ok["id"])],
    )
