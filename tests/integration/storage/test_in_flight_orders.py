"""Migration 0013 (specs/009-pending-orders research I1, ADR 0020): the view
in_flight_orders shows the Risk Gate which of its approvals are still working,
without giving it the orders or execution_refusals tables."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from tests.integration.helpers import as_role, attempt
from tests.integration.storage.chain import (
    insert_decision,
    insert_refusal,
    insert_report,
    order_id_for,
)

COLUMNS = ["trading_day", "symbol", "side", "unsettled_qty", "limit_price"]


def _verdict(conn, *, side="buy", symbol="AAPL", qty=10, ceiling="202.00", day="2026-09-28"):
    """An approved verdict whose approved_order is shaped like the gate's own."""
    body = {"symbol": symbol, "side": side, "qty": qty, "time_in_force": "day"}
    if side == "buy":
        body["limit_price"] = ceiling
    decision = insert_decision(conn, [insert_report(conn)], symbol=symbol)
    return conn.execute(
        "INSERT INTO risk_verdicts (decision_id, verdict, approved_order, trading_day, "
        "config_version) VALUES (%s, 'approved', %s::jsonb, %s, 'test') RETURNING id",
        (decision, json.dumps(body), day),
    ).fetchone()["id"]


def _order(conn, verdict, *, side="buy", symbol="AAPL", status="submitted", fill_qty=None):
    conn.execute(
        "INSERT INTO orders (id, risk_verdict_id, status, limit_price, fill_qty) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            order_id_for(verdict, side, symbol),
            verdict,
            status,
            "202" if side == "buy" else None,
            fill_qty,
        ),
    )


def _rows(conn, role="ta_risk_gate"):
    with as_role(conn, role):
        return conn.execute(
            "SELECT trading_day, symbol, side, unsettled_qty, limit_price "
            "FROM in_flight_orders ORDER BY symbol, side"
        ).fetchall()


def test_view_exposes_exactly_five_columns(conn):
    with as_role(conn, "ta_risk_gate"):
        cur = conn.execute("SELECT * FROM in_flight_orders WHERE false")
        assert [c.name for c in cur.description] == COLUMNS


def test_approval_with_no_outcome_is_in_flight(conn):
    _verdict(conn, qty=24, ceiling="202.00")
    (row,) = _rows(conn)
    assert row["symbol"] == "AAPL" and row["side"] == "buy"
    assert row["unsettled_qty"] == 24
    assert row["limit_price"] == Decimal("202.00")
    assert str(row["trading_day"]) == "2026-09-28"


def test_submitted_order_shows_its_full_qty(conn):
    _order(conn, _verdict(conn, qty=24))
    (row,) = _rows(conn)
    assert row["unsettled_qty"] == 24


def test_partially_filled_order_shows_the_remainder(conn):
    _order(conn, _verdict(conn, qty=24), status="partially_filled", fill_qty=10)
    (row,) = _rows(conn)
    assert row["unsettled_qty"] == 14


def test_sell_has_no_limit_price(conn):
    _verdict(conn, side="sell", qty=30)
    (row,) = _rows(conn)
    assert row["side"] == "sell" and row["unsettled_qty"] == 30
    assert row["limit_price"] is None


@pytest.mark.parametrize("status", ["filled", "rejected", "canceled", "expired"])
def test_ended_orders_are_left_out(conn, status):
    _order(conn, _verdict(conn), status=status, fill_qty=10 if status == "filled" else None)
    assert _rows(conn) == []


def test_refused_approval_is_left_out(conn):
    insert_refusal(conn, _verdict(conn))
    assert _rows(conn) == []


def test_rejected_verdict_is_left_out(conn):
    decision = insert_decision(conn, [insert_report(conn)])
    conn.execute(
        "INSERT INTO risk_verdicts (decision_id, verdict, rejection_rule, trading_day, "
        "config_version) VALUES (%s, 'rejected', 'max_position_pct', '2026-09-28', 'test')",
        (decision,),
    )
    assert _rows(conn) == []


def test_fully_filled_remainder_of_zero_is_left_out(conn):
    _order(conn, _verdict(conn, qty=24), status="partially_filled", fill_qty=24)
    assert _rows(conn) == []


def test_each_row_carries_its_verdicts_trading_day(conn):
    _verdict(conn, symbol="AAA", day="2026-09-25")
    _verdict(conn, symbol="BBB", day="2026-09-28")
    days = {r["symbol"]: str(r["trading_day"]) for r in _rows(conn)}
    assert days == {"AAA": "2026-09-25", "BBB": "2026-09-28"}


@pytest.mark.parametrize("role", ["ta_risk_gate", "ta_assistant", "ta_dashboard"])
def test_readers_can_select(conn, role):
    assert attempt(conn, role, "SELECT * FROM in_flight_orders") == "allowed"


@pytest.mark.parametrize("table", ["orders", "execution_refusals"])
def test_gate_still_cannot_read_the_base_tables(conn, table):
    assert attempt(conn, "ta_risk_gate", f"SELECT * FROM {table}") == "denied"


@pytest.mark.parametrize("role", ["ta_risk_gate", "ta_assistant", "ta_dashboard"])
@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE", "DELETE", "TRUNCATE"])
def test_no_role_gained_a_write(conn, role, privilege):
    # The view joins, so Postgres can't write through it anyway; the privilege
    # itself must also be absent.
    row = conn.execute(
        "SELECT has_table_privilege(%s, 'in_flight_orders', %s) AS held", (role, privilege)
    ).fetchone()
    assert row["held"] is False
