"""Concise constructors for Risk Gate unit tests.

Defaults describe a healthy open market on the test clock (Monday 2026-09-28,
10:00 ET): no position, $100,000 equity, all cash, baseline equal to equity, no
halt, no pause, no orders yet today, and a symbol that passes every universe rule.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from trading_agent.risk.config import RiskConfig, load_config
from trading_agent.risk.model import (
    Context,
    DecisionRequest,
    Reference,
    StopLossRequest,
)

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
TODAY = date(2026, 9, 28)
REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "risk.yaml"

PASSING_REFERENCE = Reference(
    security_type="common_stock",
    exchange_mic="XNAS",
    market_cap_usd=Decimal("3000000000000"),
    avg_daily_dollar_volume_usd=Decimal("5000000000"),
    share_price_usd=Decimal("200"),
)


def D(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


_IN_FLIGHT = (
    "in_flight_buy_qty",
    "in_flight_buy_cost_symbol",
    "in_flight_sell_qty",
    "in_flight_buy_cost_all",
)


def context(**overrides) -> Context:
    values = {
        "now": NOW,
        "trading_day": TODAY,
        "market_open": True,
        "trading_paused": False,
        "halt_active": False,
        "shares_held": 0,
        "avg_entry_price": None,
        "equity": D(100000),
        "cash": D(100000),
        "baseline_equity": D(100000),
        "increase_orders_approved_today": 0,
        "reference": PASSING_REFERENCE,
        "in_flight_buy_qty": 0,
        "in_flight_buy_cost_symbol": 0,
        "in_flight_sell_qty": 0,
        "in_flight_buy_cost_all": 0,
    }
    values.update(overrides)
    for key in ("equity", "cash", "baseline_equity", "avg_entry_price"):
        if values[key] is not None:
            values[key] = D(values[key])
    for key in _IN_FLIGHT:
        values[key] = D(values[key])
    return Context(**values)


_TEXT_FIELDS = {"security_type", "exchange_mic"}


def reference(**overrides) -> Reference:
    return dataclasses.replace(
        PASSING_REFERENCE, **{k: v if k in _TEXT_FIELDS else D(v) for k, v in overrides.items()}
    )


def buy(symbol="AAPL", target=5, quote=200, quote_time: datetime = NOW) -> DecisionRequest:
    return DecisionRequest(symbol, "buy", D(target), D(quote), quote_time)


def sell(symbol="AAPL", target=0, quote=200, quote_time: datetime = NOW) -> DecisionRequest:
    return DecisionRequest(symbol, "sell", D(target), D(quote), quote_time)


def trigger(symbol="AAPL", observed=160, observed_at: datetime = NOW) -> StopLossRequest:
    return StopLossRequest(symbol, D(observed), observed_at)


def config(**overrides) -> RiskConfig:
    base = load_config(REPO_CONFIG)
    converted = {
        k: v
        if isinstance(v, int) and not isinstance(v, bool) and k == "max_orders_per_day"
        else D(v)
        for k, v in overrides.items()
    }
    return dataclasses.replace(base, **converted)
