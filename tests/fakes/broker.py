"""An in-memory broker for every Execution test (specs/003-execution/contracts/broker-port.md).

Nothing here touches a network. Tests script the account, positions, quotes and
trades; submissions are recorded; fills, close-outs, rejections and outages
happen only when a test asks for them.
"""

from __future__ import annotations

import dataclasses
import itertools
from datetime import UTC, datetime
from decimal import Decimal

from trading_agent.execution.broker import (
    Account,
    BrokerOrder,
    BrokerPosition,
    BrokerUnavailable,
    OrderRejected,
    OrderRequest,
    Quote,
    Trade,
)

FINAL = {"filled", "canceled", "expired", "rejected", "done_for_day"}


def D(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class FakeBroker:
    def __init__(
        self,
        *,
        equity="100000",
        cash="100000",
        now: datetime = datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        account_number: str = "PA-FAKE-0001",
    ) -> None:
        self.now = now
        self.account = Account(account_number, D(equity), D(cash), D(cash))
        self.positions: dict[str, BrokerPosition] = {}
        self.quotes: dict[str, Quote] = {}
        self.trades: dict[str, Trade] = {}
        self.orders: dict[str, BrokerOrder] = {}  # broker id -> order
        self.by_client_id: dict[str, list[str]] = {}
        self.submissions: list[OrderRequest] = []
        self.calls: list[str] = []
        self.reject_duplicate_client_ids = False
        self._failures: dict[str, bool] = {}  # method -> after_effect
        self._reject_next: str | None = None
        self._hidden: set[str] = set()  # client ids find_order can't see yet (lag)
        self._ids = itertools.count(1)

    # --- scripting -------------------------------------------------------

    def set_account(self, *, equity=None, cash=None, buying_power=None) -> None:
        a = self.account
        self.account = dataclasses.replace(
            a,
            equity=a.equity if equity is None else D(equity),
            cash=a.cash if cash is None else D(cash),
            buying_power=a.buying_power if buying_power is None else D(buying_power),
        )

    def set_position(self, symbol: str, qty, avg_entry_price) -> None:
        self.positions[symbol] = BrokerPosition(symbol, D(qty), D(avg_entry_price))

    def set_quote(self, symbol: str, ask, at: datetime | None = None) -> None:
        self.quotes[symbol] = Quote(symbol, D(ask), at or self.now)

    def set_trade(self, symbol: str, price, at: datetime | None = None) -> None:
        self.trades[symbol] = Trade(symbol, D(price), at or self.now)

    def fail(self, method: str, *, after_effect: bool = False) -> None:
        """The next call to `method` raises BrokerUnavailable. With after_effect on
        submit_order, the order is placed first (a 'maybe placed' timeout)."""
        self._failures[method] = after_effect

    def reject_next(self, reason: str = "insufficient buying power") -> None:
        self._reject_next = reason

    def hide_from_lookup(self, client_order_id: str) -> None:
        self._hidden.add(client_order_id)

    def reveal(self, client_order_id: str) -> None:
        self._hidden.discard(client_order_id)

    def calls_named(self, name: str) -> list[str]:
        return [call for call in self.calls if call == name]

    def orders_for(self, client_order_id: str) -> list[BrokerOrder]:
        return [self.orders[i] for i in self.by_client_id.get(client_order_id, [])]

    def fill(self, client_order_id: str, qty, price) -> BrokerOrder:
        """Fill `qty` more shares at `price`, as the broker would."""
        order = self.orders_for(client_order_id)[0]
        qty, price = D(qty), D(price)
        new_filled = order.filled_qty + qty
        if new_filled > order.qty:
            raise ValueError("overfill")
        old_value = order.filled_qty * (order.filled_avg_price or 0)
        avg = (old_value + qty * price) / new_filled
        status = "filled" if new_filled == order.qty else "partially_filled"
        order = dataclasses.replace(
            order, filled_qty=new_filled, filled_avg_price=avg, status=status
        )
        self.orders[order.broker_order_id] = order
        self._apply_to_book(order.symbol, order.side, qty, price)
        return order

    def close_session(self) -> None:
        for broker_id, order in list(self.orders.items()):
            if order.status in FINAL:
                continue
            status = "done_for_day" if order.filled_qty > 0 else "canceled"
            self.orders[broker_id] = dataclasses.replace(order, status=status)

    def _apply_to_book(self, symbol: str, side: str, qty: Decimal, price: Decimal) -> None:
        held = self.positions.get(symbol)
        cash = self.account.cash
        if side == "buy":
            if held is None:
                self.positions[symbol] = BrokerPosition(symbol, qty, price)
            else:
                total = held.qty + qty
                avg = (held.qty * held.avg_entry_price + qty * price) / total
                self.positions[symbol] = BrokerPosition(symbol, total, avg)
            cash -= qty * price
        else:
            remaining = held.qty - qty
            if remaining == 0:
                del self.positions[symbol]
            else:
                self.positions[symbol] = dataclasses.replace(held, qty=remaining)
            cash += qty * price
        self.account = dataclasses.replace(self.account, cash=cash)

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name in self._failures:
            del self._failures[name]
            raise BrokerUnavailable(f"fake outage in {name}")

    # --- the Broker protocol ---------------------------------------------

    def get_account(self) -> Account:
        self._call("get_account")
        return self.account

    def get_positions(self) -> list[BrokerPosition]:
        self._call("get_positions")
        return list(self.positions.values())

    def get_latest_ask(self, symbol: str) -> Quote:
        self._call("get_latest_ask")
        if symbol not in self.quotes:
            raise BrokerUnavailable(f"no quote for {symbol}")
        return self.quotes[symbol]

    def get_latest_trade(self, symbol: str) -> Trade:
        self._call("get_latest_trade")
        if symbol not in self.trades:
            raise BrokerUnavailable(f"no trade for {symbol}")
        return self.trades[symbol]

    def find_order(self, client_order_id: str) -> BrokerOrder | None:
        self._call("find_order")
        if client_order_id in self._hidden:
            return None
        found = self.orders_for(client_order_id)
        return found[0] if found else None

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        self._call("get_order")
        return self.orders[broker_order_id]

    def submit_order(self, request: OrderRequest) -> BrokerOrder:
        self.calls.append("submit_order")
        after_effect = self._failures.pop("submit_order", None)
        if after_effect is False:
            raise BrokerUnavailable("fake outage in submit_order")
        if self._reject_next is not None:
            reason, self._reject_next = self._reject_next, None
            raise OrderRejected(reason)
        if self.reject_duplicate_client_ids and request.client_order_id in self.by_client_id:
            # Rejecting a duplicate means the broker knows the order: its lookup
            # has caught up by now.
            self._hidden.discard(request.client_order_id)
            raise OrderRejected("client_order_id must be unique")
        self.submissions.append(request)
        order = BrokerOrder(
            broker_order_id=f"broker-{next(self._ids)}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=D(request.qty),
            order_type=request.order_type,
            limit_price=request.limit_price,
            status="new",
            filled_qty=Decimal(0),
            filled_avg_price=None,
            submitted_at=self.now,
        )
        self.orders[order.broker_order_id] = order
        self.by_client_id.setdefault(request.client_order_id, []).append(order.broker_order_id)
        if after_effect is True:
            raise BrokerUnavailable("fake timeout in submit_order (order was placed)")
        return order
