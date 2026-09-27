"""Plain values Execution's pure core takes and returns.

Everything numeric is a Decimal; nothing here performs I/O
(specs/003-execution/research.md E1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from trading_agent.execution.broker import OrderRequest, Quote
from trading_agent.risk.config import RiskConfig
from trading_agent.risk.model import ApprovedOrder


@dataclass(frozen=True)
class Approval:
    verdict_id: UUID
    order: ApprovedOrder


@dataclass(frozen=True)
class Session:
    """Where `now` sits in the trading calendar, computed by the caller."""

    now: datetime
    today: date
    market_open: bool
    after_close: bool  # today is a session and its close has passed, or today isn't a session


@dataclass(frozen=True)
class BuyLive:
    """What Execution read just before judging a buy (research E6).

    `equity`/`cash` are None until the account has been fetched; the pre-fetch
    checks (rows 1-6) never look at them.
    """

    paused: bool
    baseline: Decimal | None
    config: RiskConfig | None
    equity: Decimal | None = None
    cash: Decimal | None = None
    min_equity_since_open: Decimal | None = None
    held_qty: Decimal = Decimal(0)
    open_buy_qty_symbol: Decimal = Decimal(0)
    open_buy_cost_all: Decimal = Decimal(0)
    ask: Quote | None = None


@dataclass(frozen=True)
class ExitLive:
    held_qty: Decimal
    open_sell_qty_symbol: Decimal


@dataclass(frozen=True)
class Submit:
    request: OrderRequest


@dataclass(frozen=True)
class Refuse:
    reason: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Retry:
    why: str


Outcome = Submit | Refuse | Retry


@dataclass
class TickReport:
    submitted: int = 0
    recovered: int = 0
    refused: int = 0
    retried: int = 0
    errors: int = 0
    fills_applied: int = 0
    reconciled: int = 0
    triggers: int = 0
    unevaluated_triggers: int = 0
    snapshot_taken: bool = False
