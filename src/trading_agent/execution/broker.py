"""The broker port: the only way Execution talks to the broker.

One real implementation (`trading_agent.execution.alpaca`) and one fake
(`tests/fakes/broker.py`). Values are the project's own frozen dataclasses with
Decimals, never SDK objects (specs/003-execution/contracts/broker-port.md).
There is deliberately no cancel, replace or close-position call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol


class BrokerUnavailable(Exception):
    """The broker couldn't be reached or answered badly. On submit_order this
    means the order may or may not have been placed (research E5)."""


class OrderRejected(Exception):
    """The broker refused the order outright; nothing was placed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NotPaperTrading(Exception):
    """Execution is not provably connected to the paper account (FR-013)."""


@dataclass(frozen=True)
class Account:
    account_number: str
    equity: Decimal
    cash: Decimal
    buying_power: Decimal


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal


@dataclass(frozen=True)
class Quote:
    symbol: str
    ask: Decimal  # 0 means no active ask
    timestamp: datetime
    # What a market sell would get: the stop-loss monitor's second reading (ADR 0014).
    # 0 means no active bid.
    bid: Decimal = Decimal(0)


@dataclass(frozen=True)
class Trade:
    symbol: str
    price: Decimal
    timestamp: datetime


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["limit", "market"]
    limit_price: Decimal | None = None
    time_in_force: Literal["day"] = "day"

    def __post_init__(self) -> None:
        if (self.order_type == "limit") != (self.limit_price is not None):
            raise ValueError("a limit order needs a limit price; a market order has none")
        if self.qty < 1:
            raise ValueError("an order is at least one whole share")


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: Decimal
    order_type: Literal["limit", "market"]
    limit_price: Decimal | None
    status: str  # the broker's raw status string (research E7)
    filled_qty: Decimal
    filled_avg_price: Decimal | None
    submitted_at: datetime
    reason: str | None = None


class Broker(Protocol):
    def verify_paper(self) -> None:
        """Raise NotPaperTrading unless provably on the paper account (FR-013)."""

    def get_account(self) -> Account: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def get_latest_quote(self, symbol: str) -> Quote: ...

    def get_latest_trade(self, symbol: str) -> Trade: ...

    def find_order(self, client_order_id: str) -> BrokerOrder | None: ...

    def get_order(self, broker_order_id: str) -> BrokerOrder: ...

    def submit_order(self, request: OrderRequest) -> BrokerOrder: ...
