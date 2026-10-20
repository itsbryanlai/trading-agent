"""Order-history rows as the store returns them, for the view and route tests (T021)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

AT = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)

BASE = {
    "id": "v-1",
    "evaluated_at": AT,
    "trading_day": AT.date(),
    "verdict": "rejected",
    "rejection_rule": "trading_paused",
    "approved_order": None,
    "decision_id": "d-1",
    "decided_at": AT,
    "d_symbol": "AAPL",
    "direction": "buy",
    "size_pct": Decimal("5.000"),
    "quote_at_decision": Decimal("187.2500"),
    "quote_time": AT,
    "trigger_id": None,
    "t_symbol": None,
    "observed_price": None,
    "observed_at": None,
    "order_id": None,
    "status": None,
    "limit_price": None,
    "fill_qty": None,
    "fill_price": None,
    "broker_reason": None,
    "submitted_at": None,
    "refusal_reason": None,
    "refusal_details": None,
    "refused_at": None,
}
APPROVED = {
    "verdict": "approved",
    "rejection_rule": None,
    "approved_order": {"symbol": "AAPL", "side": "buy", "qty": 10, "limit_price": 187.25},
}


def row(**changes) -> dict:
    return {**BASE, **changes}


def rejected(**changes) -> dict:
    return row(**changes)


def filled(**changes) -> dict:
    return row(
        **{
            **APPROVED,
            "order_id": "o-1",
            "status": "filled",
            "limit_price": Decimal("187.2500"),
            "fill_qty": Decimal("10.0000"),
            "fill_price": Decimal("187.1000"),
            "submitted_at": AT,
            **changes,
        }
    )


def refused(**changes) -> dict:
    return row(
        **{
            **APPROVED,
            "refusal_reason": "quote_above_ceiling",
            "refusal_details": {"ceiling": "190.00", "quote": "191.5"},
            "refused_at": AT,
            **changes,
        }
    )


def stop_loss(**changes) -> dict:
    return row(
        **{
            **APPROVED,
            "decision_id": None,
            "decided_at": None,
            "d_symbol": None,
            "direction": None,
            "size_pct": None,
            "quote_at_decision": None,
            "trigger_id": "t-1",
            "t_symbol": "MU",
            "observed_price": Decimal("160.0000"),
            "observed_at": AT,
            **changes,
        }
    )


def in_flight(**changes) -> dict:
    return row(**{**APPROVED, **changes})
