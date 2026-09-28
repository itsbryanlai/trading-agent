"""Order status mapping and fill arithmetic (research E7, E8). Pure."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

FINAL = frozenset({"filled", "rejected", "canceled", "expired"})

_STILL_WORKING = frozenset(
    {
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "held",
        "calculated",
        "stopped",
        "suspended",
        "pending_cancel",
        "pending_replace",
    }
)
_DIRECT = {
    "partially_filled": "partially_filled",
    "filled": "filled",
    "done_for_day": "expired",
    "expired": "expired",
    "canceled": "canceled",
    "rejected": "rejected",
}


class UnexpectedStatus(Exception):
    """A broker status Execution never causes (e.g. `replaced`) or doesn't know."""


def map_status(raw: str, filled_qty: Decimal) -> str:
    if raw in _STILL_WORKING:
        return "partially_filled" if filled_qty > 0 else "submitted"
    if raw in _DIRECT:
        return _DIRECT[raw]
    raise UnexpectedStatus(raw)


def is_final(status: str) -> bool:
    return status in FINAL


def fill_delta(
    f0: Decimal, p0: Decimal | None, f1: Decimal, p1: Decimal | None
) -> tuple[Decimal, Decimal] | None:
    """The fill between two cumulative readings: (qty, price), or None if nothing new."""
    dq = f1 - f0
    if dq <= 0:
        return None
    value0 = f0 * (p0 or 0)
    return dq, (f1 * p1 - value0) / dq


@dataclass(frozen=True)
class Holding:
    qty: Decimal
    avg_entry_price: Decimal


def apply_fill(
    holding: Holding | None, side: Literal["buy", "sell"], qty: Decimal, price: Decimal
) -> Holding | None:
    """A buy adds at a weighted average; a sell reduces and keeps the average;
    nothing left means no position (None)."""
    if side == "buy":
        if holding is None:
            return Holding(qty, price)
        total = holding.qty + qty
        avg = (holding.qty * holding.avg_entry_price + qty * price) / total
        return Holding(total, avg)
    if holding is None:
        raise ValueError("sell fill with no position")
    remaining = holding.qty - qty
    if remaining < 0:
        raise ValueError("sell fill larger than the position")
    return None if remaining == 0 else Holding(remaining, holding.avg_entry_price)
