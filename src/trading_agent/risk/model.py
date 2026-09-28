"""Plain values the Risk Gate's pure core takes and returns.

Everything numeric is a Decimal. Nothing here performs I/O
(specs/002-risk-gate/contracts/gate-interface.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class DecisionRequest:
    symbol: str
    direction: Literal["buy", "sell"]
    target_weight_pct: Decimal
    quote: Decimal


@dataclass(frozen=True)
class StopLossRequest:
    symbol: str
    observed_price: Decimal
    observed_at: datetime  # a trigger older than MAX_TRIGGER_AGE is stale (ADR 0014)


Request = DecisionRequest | StopLossRequest


@dataclass(frozen=True)
class Reference:
    security_type: str
    exchange_mic: str
    market_cap_usd: Decimal
    avg_daily_dollar_volume_usd: Decimal
    share_price_usd: Decimal


@dataclass(frozen=True)
class Context:
    now: datetime
    trading_day: date
    market_open: bool
    trading_paused: bool
    halt_active: bool
    shares_held: int
    avg_entry_price: Decimal | None
    equity: Decimal | None
    cash: Decimal | None
    baseline_equity: Decimal | None
    increase_orders_approved_today: int
    reference: Reference | None
    # The lowest equity among today's snapshots since the open, so a crossing between
    # two evaluations still records the halt (ADR 0014 §3, second review F7).
    lowest_equity_today: Decimal | None = None


@dataclass(frozen=True)
class ApprovedOrder:
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["limit", "market"]
    limit_price: Decimal | None
    trading_day: date
    exposure: Literal["increase", "decrease"]
    source: Literal["decision", "stop_loss"]
    trims: tuple[str, ...] = ()
    time_in_force: Literal["day"] = "day"

    def to_json(self) -> dict:
        body = {
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "trading_day": self.trading_day.isoformat(),
            "exposure": self.exposure,
            "trims": list(self.trims),
            "source": self.source,
        }
        if self.limit_price is not None:
            body["limit_price"] = str(self.limit_price)
        return body

    @classmethod
    def from_json(cls, body: dict) -> ApprovedOrder:
        return cls(
            symbol=body["symbol"],
            side=body["side"],
            qty=int(body["qty"]),
            order_type=body["order_type"],
            limit_price=Decimal(body["limit_price"]) if "limit_price" in body else None,
            trading_day=date.fromisoformat(body["trading_day"]),
            exposure=body["exposure"],
            source=body["source"],
            trims=tuple(body["trims"]),
            time_in_force=body["time_in_force"],
        )


@dataclass(frozen=True)
class Verdict:
    approved: bool
    rejection_rule: str | None = None
    order: ApprovedOrder | None = None

    def __post_init__(self) -> None:
        if self.approved != (self.order is not None) or self.approved == (
            self.rejection_rule is not None
        ):
            raise ValueError("a verdict carries an order if approved, a rule if rejected")

    @classmethod
    def approve(cls, order: ApprovedOrder) -> Verdict:
        return cls(approved=True, order=order)

    @classmethod
    def reject(cls, rule: str) -> Verdict:
        return cls(approved=False, rejection_rule=rule)


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    config_version: str
    trading_day: date
    record_halt: bool = field(default=False)
