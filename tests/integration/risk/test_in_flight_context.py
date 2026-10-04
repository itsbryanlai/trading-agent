"""_load_context reads today's in-flight approvals into the four Context numbers
(specs/009-pending-orders research I2, I3; ADR 0020)."""

from __future__ import annotations

import json
from decimal import Decimal

from psycopg.rows import dict_row

from tests.integration.helpers import as_role
from tests.integration.risk.conftest import NOW
from tests.integration.storage.chain import insert_decision, insert_report
from trading_agent.risk.service import _load_context


def _approve(conn, symbol, side, qty, ceiling=None, day="2026-09-28"):
    body = {"symbol": symbol, "side": side, "qty": qty, "time_in_force": "day"}
    if ceiling is not None:
        body["limit_price"] = ceiling
    decision = insert_decision(conn, [insert_report(conn, symbol=symbol)], symbol=symbol)
    conn.execute(
        "INSERT INTO risk_verdicts (decision_id, verdict, approved_order, trading_day, "
        "config_version) VALUES (%s, 'approved', %s::jsonb, %s, 'test')",
        (decision, json.dumps(body), day),
    )


def _numbers(conn, symbol):
    with as_role(conn, "ta_risk_gate"), conn.cursor(row_factory=dict_row) as cur:
        context, _ = _load_context(cur, symbol, NOW)
    return (
        context.in_flight_buy_qty,
        context.in_flight_buy_cost_symbol,
        context.in_flight_sell_qty,
        context.in_flight_buy_cost_all,
    )


def test_symbol_numbers_and_the_all_symbol_cost(conn):
    _approve(conn, "AAPL", "buy", 24, "202.00")
    _approve(conn, "AAPL", "sell", 30)
    _approve(conn, "MSFT", "buy", 10, "410.00")

    assert _numbers(conn, "AAPL") == (24, Decimal("4848.00"), 30, Decimal("8948.00"))
    assert _numbers(conn, "MSFT") == (10, Decimal("4100.00"), 0, Decimal("8948.00"))


def test_an_earlier_days_approval_is_not_in_flight(conn):
    _approve(conn, "AAPL", "buy", 24, "202.00", day="2026-09-25")
    _approve(conn, "AAPL", "sell", 5, day="2026-09-25")

    assert _numbers(conn, "AAPL") == (0, 0, 0, 0)


def test_nothing_in_flight_gives_zeros(conn):
    assert _numbers(conn, "AAPL") == (0, 0, 0, 0)
