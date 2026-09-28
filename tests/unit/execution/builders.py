"""Concise constructors for Execution unit tests.

Defaults describe a buy that passes every check on the test clock (Monday
2026-09-28, 10:00 ET): $100,000 equity, all cash, nothing held, no open orders,
a fresh ask of $201.50 under a $202 ceiling, baseline equal to equity.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from trading_agent.execution.broker import Quote
from trading_agent.execution.model import Approval, BuyLive, ExitLive, Session
from trading_agent.risk.config import RiskConfig, load_config
from trading_agent.risk.model import ApprovedOrder

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
TODAY = date(2026, 9, 28)
REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "risk.yaml"
VERDICT = UUID("3f9c2a1b-0000-4000-8000-000000000001")


def D(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def repo_config(**overrides) -> RiskConfig:
    config = load_config(REPO_CONFIG)
    return dataclasses.replace(config, **{k: D(v) for k, v in overrides.items()})


def session(now: datetime = NOW, *, market_open=True, after_close=False) -> Session:
    return Session(now=now, today=now.date(), market_open=market_open, after_close=after_close)


def approved_buy(
    qty=24, ceiling="202", symbol="AAPL", day: date = TODAY, verdict_id: UUID = VERDICT
) -> Approval:
    return Approval(
        verdict_id,
        ApprovedOrder(
            symbol=symbol,
            side="buy",
            qty=qty,
            order_type="limit",
            limit_price=D(ceiling),
            trading_day=day,
            exposure="increase",
            source="decision",
        ),
    )


def approved_sell(
    qty=50, symbol="AAPL", source="decision", day: date = TODAY, verdict_id: UUID = VERDICT
) -> Approval:
    return Approval(
        verdict_id,
        ApprovedOrder(
            symbol=symbol,
            side="sell",
            qty=qty,
            order_type="market",
            limit_price=None,
            trading_day=day,
            exposure="decrease",
            source=source,
        ),
    )


def quote(ask="201.50", at: datetime = NOW, symbol="AAPL") -> Quote:
    return Quote(symbol, D(ask), at)


def buy_live(**overrides) -> BuyLive:
    values = {
        "paused": False,
        "baseline": D(100000),
        "config": repo_config(),
        "equity": D(100000),
        "cash": D(100000),
        "min_equity_since_open": None,
        "held_qty": D(0),
        "open_buy_qty_symbol": D(0),
        "open_buy_cost_all": D(0),
        "ask": quote(),
    }
    for key, value in overrides.items():
        values[key] = D(value) if isinstance(value, (int, str)) and key != "paused" else value
    return BuyLive(**values)


def exit_live(held=50, open_sell=0) -> ExitLive:
    return ExitLive(held_qty=D(held), open_sell_qty_symbol=D(open_sell))
