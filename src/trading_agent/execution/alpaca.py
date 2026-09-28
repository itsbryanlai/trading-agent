"""The real broker adapter: Alpaca's paper account, and nothing else.

The only module in the system that imports the broker SDK (an import-scan test
enforces it). Paper-only by construction (specs/003-execution research E2):

1. the trading address is the constant below, passed explicitly, never taken
   from configuration or the SDK's own default;
2. a configured ALPACA_BASE_URL that differs from it stops construction before
   any client exists;
3. `verify_paper()` must complete one authenticated account read at that
   address before Execution does anything. Alpaca documents no account field
   that marks an account as paper; paper keys differ from live keys and this
   address serves only paper accounts, so a successful read here is the proof.

No retries here: the SDK's own retries are switched off, because retrying an
order submission that timed out could place it twice. Retrying is the tick's
job, and it always looks the order up first (research E5).
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from trading_agent.execution.broker import (
    Account,
    BrokerOrder,
    BrokerPosition,
    BrokerUnavailable,
    NotPaperTrading,
    OrderRejected,
    OrderRequest,
    Quote,
    Trade,
)

log = logging.getLogger(__name__)

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"

# Every HTTP call gives up after this many seconds (the SDK sets none).
REQUEST_TIMEOUT_SECONDS = 10

# Submission errors that mean "refused outright, nothing placed" (broker-port.md).
_REJECTED_STATUSES = frozenset({403, 422})


def _no_retries_and_a_timeout(client) -> None:
    """Switch off the SDK's retry loop (it retries 429 and 504, and a 504 on an
    order may mean it was placed) and give every request a timeout."""
    client._retry = 0
    client._retry_codes = []
    client._session.request = functools.partial(
        client._session.request, timeout=REQUEST_TIMEOUT_SECONDS
    )


def _dec(value) -> Decimal:
    if value is None:
        raise BrokerUnavailable("broker returned no value where one was required")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise BrokerUnavailable(f"unparseable number from broker: {value!r}") from exc


def _dec_or_none(value) -> Decimal | None:
    return None if value in (None, "") else _dec(value)


def _enum_value(value) -> str:
    return getattr(value, "value", value)


class AlpacaBroker:
    def __init__(self, key_id: str, secret_key: str, configured_base_url: str | None) -> None:
        if configured_base_url and configured_base_url != PAPER_TRADING_URL:
            raise NotPaperTrading(
                f"ALPACA_BASE_URL must be {PAPER_TRADING_URL} or unset; refusing to start"
            )
        self._trading = TradingClient(
            key_id, secret_key, paper=True, url_override=PAPER_TRADING_URL
        )
        self._data = StockHistoricalDataClient(key_id, secret_key)
        _no_retries_and_a_timeout(self._trading)
        _no_retries_and_a_timeout(self._data)

    def verify_paper(self) -> None:
        """One authenticated read at the fixed paper address, or refuse to start."""
        try:
            account = self._trading.get_account()
        except Exception as exc:
            raise NotPaperTrading(f"account check at {PAPER_TRADING_URL} failed: {exc}") from exc
        log.info("execution: paper account %s verified", account.account_number)

    # --- the Broker protocol ---------------------------------------------

    def get_account(self) -> Account:
        account = self._call(self._trading.get_account)
        return Account(
            account_number=str(account.account_number),
            equity=_dec(account.equity),
            cash=_dec(account.cash),
            buying_power=_dec(account.buying_power),
        )

    def get_positions(self) -> list[BrokerPosition]:
        positions = self._call(self._trading.get_all_positions)
        return [BrokerPosition(p.symbol, _dec(p.qty), _dec(p.avg_entry_price)) for p in positions]

    def get_latest_quote(self, symbol: str) -> Quote:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        # Indexed inside _call: a symbol missing from the reply is BrokerUnavailable,
        # never a raw KeyError (second review F6).
        quote = self._call(lambda: self._data.get_stock_latest_quote(request)[symbol])
        return Quote(symbol, _dec(quote.ask_price), quote.timestamp, bid=_dec(quote.bid_price))

    def get_latest_trade(self, symbol: str) -> Trade:
        request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        trade = self._call(lambda: self._data.get_stock_latest_trade(request)[symbol])
        return Trade(symbol, _dec(trade.price), trade.timestamp)

    def find_order(self, client_order_id: str) -> BrokerOrder | None:
        try:
            return self._to_order(self._trading.get_order_by_client_id(client_order_id))
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise BrokerUnavailable(f"order lookup failed: {exc}") from exc
        except Exception as exc:
            raise BrokerUnavailable(f"order lookup failed: {exc}") from exc

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        return self._to_order(self._call(self._trading.get_order_by_id, broker_order_id))

    def submit_order(self, request: OrderRequest) -> BrokerOrder:
        side = OrderSide.BUY if request.side == "buy" else OrderSide.SELL
        common = {
            "symbol": request.symbol,
            "qty": request.qty,
            "side": side,
            "time_in_force": TimeInForce.DAY,
            "client_order_id": request.client_order_id,
        }
        if request.order_type == "limit":
            order_data = LimitOrderRequest(limit_price=float(request.limit_price), **common)
        else:
            order_data = MarketOrderRequest(**common)
        try:
            placed = self._trading.submit_order(order_data)
        except APIError as exc:
            if exc.status_code in _REJECTED_STATUSES:
                raise OrderRejected(str(exc)) from exc
            raise BrokerUnavailable(f"order submission failed, may be placed: {exc}") from exc
        except Exception as exc:
            raise BrokerUnavailable(f"order submission failed, may be placed: {exc}") from exc
        return self._to_order(placed)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _call(method, *args):
        try:
            return method(*args)
        except (APIError, requests.RequestException, KeyError, ValueError) as exc:
            raise BrokerUnavailable(f"{getattr(method, '__name__', method)} failed: {exc}") from exc

    @staticmethod
    def _to_order(order) -> BrokerOrder:
        submitted: datetime = order.submitted_at or order.created_at
        return BrokerOrder(
            broker_order_id=str(order.id),
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=_enum_value(order.side),
            qty=_dec(order.qty),
            order_type=_enum_value(order.type or order.order_type),
            limit_price=_dec_or_none(order.limit_price),
            status=_enum_value(order.status),
            filled_qty=_dec(order.filled_qty or 0),
            filled_avg_price=_dec_or_none(order.filled_avg_price),
            submitted_at=submitted,
        )
