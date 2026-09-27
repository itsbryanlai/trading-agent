"""US7: orders and positions follow what the broker confirms (FR-010, FR-011, SC-007)."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.integration.execution.conftest import (
    NOW,
    approved_verdict,
    buy_order,
    outcomes,
    run_tick,
    seed_baseline,
    sell_order,
)
from tests.integration.risk.conftest import insert_position
from tests.integration.storage.chain import order_id_for
from trading_agent.execution.service import Executor

LATER = NOW + timedelta(minutes=1)


def _position(conn, symbol="AAPL"):
    row = conn.execute(
        "SELECT qty, avg_entry_price FROM positions WHERE symbol = %s", (symbol,)
    ).fetchone()
    return None if row is None else (row["qty"], row["avg_entry_price"])


def _submitted_buy(conn, broker, executor, **order):
    seed_baseline(conn)
    verdict = approved_verdict(conn, buy_order(**order))
    run_tick(conn, executor)
    return verdict, order_id_for(verdict)


def test_a_full_fill_updates_the_order_and_creates_the_position(conn, broker, executor):
    verdict, client_id = _submitted_buy(conn, broker, executor)
    broker.fill(client_id, 24, "201.50")

    report = run_tick(conn, executor, LATER)

    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "filled"
    assert (order["fill_qty"], order["fill_price"]) == (Decimal(24), Decimal("201.5"))
    assert _position(conn) == (Decimal(24), Decimal("201.5"))
    assert report.fills_applied == 1


def test_a_partial_fill_then_the_close_leaves_the_order_expired(conn, broker, executor):
    verdict, client_id = _submitted_buy(conn, broker, executor)
    broker.fill(client_id, 10, "201")
    run_tick(conn, executor, LATER)
    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "partially_filled"

    broker.close_session()
    run_tick(conn, executor, LATER + timedelta(minutes=1))

    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "expired" and order["fill_qty"] == 10
    assert _position(conn) == (Decimal(10), Decimal(201))


def test_an_unfilled_day_order_ends_canceled(conn, broker, executor):
    verdict, _ = _submitted_buy(conn, broker, executor)
    broker.close_session()
    run_tick(conn, executor, LATER)
    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "canceled" and _position(conn) is None


def test_a_buy_on_top_of_a_position_averages_the_entry(conn, broker, executor):
    insert_position(conn, qty=24, avg_entry="200")
    broker.set_position("AAPL", 24, "200")
    _, client_id = _submitted_buy(conn, broker, executor, qty=6, ceiling="212")
    broker.set_quote("AAPL", "210")
    broker.fill(client_id, 6, "210")
    run_tick(conn, executor, LATER)
    assert _position(conn) == (Decimal(30), Decimal(202))


def test_a_full_sell_removes_the_position(conn, broker, executor):
    insert_position(conn, qty=50, avg_entry="200")
    broker.set_position("AAPL", 50, "200")
    verdict = approved_verdict(conn, sell_order(qty=50))
    run_tick(conn, executor)
    broker.fill(order_id_for(verdict, "sell"), 50, "190")
    run_tick(conn, executor, LATER)
    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "filled" and _position(conn) is None


def test_positions_that_disagree_with_the_broker_take_the_brokers_figures(
    conn, broker, executor, caplog
):
    insert_position(conn, "AAPL", qty=50, avg_entry="200")  # broker: 40 @ 205
    insert_position(conn, "TSLA", qty=5, avg_entry="250")  # broker: none
    broker.set_position("AAPL", 40, "205")
    broker.set_position("NVDA", 12, "120.123456")  # table: none

    with caplog.at_level(logging.WARNING):
        report = run_tick(conn, executor)

    assert _position(conn, "AAPL") == (Decimal(40), Decimal(205))
    assert _position(conn, "TSLA") is None
    assert _position(conn, "NVDA") == (Decimal(12), Decimal("120.1235"))
    assert report.reconciled == 3
    assert "AAPL" in caplog.text and "205" in caplog.text
    assert run_tick(conn, executor, LATER).reconciled == 0  # now in step


def test_a_short_at_the_broker_is_reported_not_written(conn, broker, executor, caplog):
    broker.set_position("AAPL", -5, "200")
    with caplog.at_level(logging.ERROR):
        run_tick(conn, executor)
    assert _position(conn) is None
    assert "short" in caplog.text


def test_a_broker_rejected_order_is_never_polled_or_resubmitted(conn, broker, executor):
    seed_baseline(conn)
    broker.reject_next("symbol halted")
    verdict = approved_verdict(conn, buy_order())
    run_tick(conn, executor)
    run_tick(conn, executor, LATER)
    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "rejected" and order["broker_reason"] == "symbol halted"
    assert "get_order" not in broker.calls and broker.submissions == []


def test_the_order_and_its_position_change_together_or_not_at_all(
    conn, broker, executor, monkeypatch
):
    _, client_id = _submitted_buy(conn, broker, executor)
    broker.fill(client_id, 24, "201.50")
    broker.positions.clear()  # keep reconciliation out of this test's picture
    original = Executor._apply_fill

    def apply_then_fail(self, *args):
        original(self, *args)
        raise RuntimeError("crash between the position and the order")

    monkeypatch.setattr(Executor, "_apply_fill", apply_then_fail)
    monkeypatch.setattr(Executor, "_reconcile_positions", lambda self, report: None)
    run_tick(conn, executor, LATER)

    [order], _ = outcomes(conn, verdict_for(conn, client_id))
    assert order["status"] == "submitted" and order["fill_qty"] is None
    assert _position(conn) is None


def verdict_for(conn, order_id):
    return conn.execute("SELECT risk_verdict_id FROM orders WHERE id = %s", (order_id,)).fetchone()[
        "risk_verdict_id"
    ]


@pytest.mark.parametrize("raw", ["replaced"])
def test_an_unexpected_status_leaves_the_order_unchanged(conn, broker, executor, caplog, raw):
    import dataclasses

    verdict, client_id = _submitted_buy(conn, broker, executor)
    [placed] = broker.orders_for(client_id)
    broker.orders[placed.broker_order_id] = dataclasses.replace(placed, status=raw)
    with caplog.at_level(logging.ERROR):
        run_tick(conn, executor, LATER)
    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "submitted" and "replaced" in caplog.text
