"""E13: one tick runs every due duty in order, exits before buys, and one failing
unit never stops the rest (analyze S5)."""

from __future__ import annotations

import logging
from datetime import date, timedelta

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


def test_duties_run_in_order_and_the_report_counts_them(conn, broker, executor):
    seed_baseline(conn)
    # An order to sync: submitted on an earlier tick, filled since.
    first = approved_verdict(conn, buy_order(qty=10))
    run_tick(conn, executor)
    broker.fill(order_id_for(first), 10, "201")
    broker.calls.clear()
    # A lapsed approval, a new exit, a new buy, and a held position to monitor.
    approved_verdict(conn, sell_order(day=date(2026, 9, 25)))
    insert_position(conn, "MSFT", qty=10, avg_entry="400")
    broker.set_position("MSFT", 10, "400")
    broker.set_trade("MSFT", "390", at=NOW + timedelta(minutes=30))
    broker.set_trade("AAPL", "201", at=NOW + timedelta(minutes=30))
    approved_verdict(conn, sell_order(qty=10, symbol="MSFT"))
    approved_verdict(conn, buy_order(qty=5))
    broker.set_quote("AAPL", "201.50", at=NOW + timedelta(minutes=30))

    report = run_tick(conn, executor, NOW + timedelta(minutes=30))

    calls = broker.calls
    assert calls.index("get_order") < calls.index("get_positions")  # sync, then reconcile
    assert calls.index("submit_order") < calls.index("get_latest_trade")  # approvals, monitor
    assert [r.side for r in broker.submissions[1:]] == ["sell", "buy"]
    assert report.fills_applied == 1
    assert report.submitted == 2
    assert report.refused == 1  # the lapsed approval
    assert report.errors == 0


def test_one_failing_approval_is_logged_and_the_others_still_run(
    conn, broker, executor, monkeypatch, caplog
):
    seed_baseline(conn)
    broker.set_position("MSFT", 10, "400")
    poisoned = approved_verdict(conn, sell_order(qty=10, symbol="MSFT"))
    fine = approved_verdict(conn, buy_order())
    real = Executor._judge

    def judge(self, cur, approval, session):
        if approval.verdict_id == poisoned:
            raise RuntimeError("poisoned approval")
        return real(self, cur, approval, session)

    monkeypatch.setattr(Executor, "_judge", judge)
    with caplog.at_level(logging.ERROR):
        report = run_tick(conn, executor)

    assert report.errors == 1 and str(poisoned) in caplog.text
    assert outcomes(conn, poisoned) == ([], [])
    assert len(outcomes(conn, fine)[0]) == 1


def test_one_failing_order_sync_does_not_stop_the_tick(conn, broker, executor, monkeypatch):
    seed_baseline(conn)
    a = approved_verdict(conn, buy_order(qty=5))
    b = approved_verdict(conn, buy_order(qty=6, symbol="MSFT"))
    broker.set_quote("MSFT", "100")
    run_tick(conn, executor)
    broker.fill(order_id_for(a), 5, "201")
    broker.fill(order_id_for(b, symbol="MSFT"), 6, "100")
    real = broker.get_order

    def flaky(broker_order_id):
        if broker_order_id == "broker-1":
            raise RuntimeError("unexpected payload")
        return real(broker_order_id)

    broker.get_order = flaky
    report = run_tick(conn, executor, NOW + timedelta(minutes=1))
    assert report.errors == 1 and report.fills_applied == 1
