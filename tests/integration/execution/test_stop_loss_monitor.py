"""US5: held positions are checked every 30 minutes; the gate evaluates triggers in
its own process; Execution submits only the exits it approved (FR-014, ADR 0013)."""

from __future__ import annotations

import logging
from datetime import timedelta

from tests.integration.execution.conftest import (
    NOW,
    gate_runner_pass,
    run_tick,
    seed_baseline,
)
from tests.integration.risk.conftest import insert_position
from trading_agent.execution.service import Executor

LATER = NOW + timedelta(minutes=1)


def _hold(conn, broker, symbol="AAPL", qty=50, entry="200", last="160"):
    insert_position(conn, symbol, qty=qty, avg_entry=entry)
    broker.set_position(symbol, qty, entry)
    broker.set_trade(symbol, last)


def _triggers(conn):
    return conn.execute(
        "SELECT symbol, observed_price FROM stop_loss_triggers ORDER BY observed_at"
    ).fetchall()


def test_a_breach_becomes_a_trigger_then_an_approved_exit_on_the_next_tick(conn, broker, executor):
    seed_baseline(conn)
    _hold(conn, broker, last="160")

    report = run_tick(conn, executor)
    assert report.triggers == 1 and broker.submissions == []
    assert [(t["symbol"], t["observed_price"]) for t in _triggers(conn)] == [("AAPL", 160)]

    assert gate_runner_pass(conn) == 1
    broker.set_trade("AAPL", "160", at=LATER)
    run_tick(conn, executor, LATER)

    [request] = broker.submissions
    assert (request.side, request.qty, request.order_type) == ("sell", 50, "market")


def test_a_cent_above_the_line_records_nothing(conn, broker, executor):
    _hold(conn, broker, last="160.01")
    assert run_tick(conn, executor).triggers == 0
    assert _triggers(conn) == []


def test_a_trigger_the_gate_rejects_leads_to_no_order(conn, broker, executor):
    seed_baseline(conn)
    _hold(conn, broker, last="160")
    run_tick(conn, executor)
    # The broker's (and so the table's) entry turns out lower: 160 is no breach.
    conn.execute("UPDATE positions SET avg_entry_price = 190 WHERE symbol = 'AAPL'")
    broker.set_position("AAPL", 50, "190")
    gate_runner_pass(conn)
    run_tick(conn, executor, LATER)
    assert broker.submissions == []


def test_no_second_trigger_while_an_exit_is_on_its_way(conn, broker, executor):
    _hold(conn, broker, last="150")
    run_tick(conn, executor)
    # A new window, but the first trigger is still unevaluated.
    run_tick(conn, executor, NOW + timedelta(minutes=31))
    assert len(_triggers(conn)) == 1


def test_one_successful_check_per_window(conn, broker, executor):
    _hold(conn, broker, last="170")
    run_tick(conn, executor)
    run_tick(conn, executor, LATER)
    assert broker.calls_named("get_latest_trade") == ["get_latest_trade"]
    broker.set_trade("AAPL", "170", at=NOW + timedelta(minutes=30))
    run_tick(conn, executor, NOW + timedelta(minutes=30))
    assert len(broker.calls_named("get_latest_trade")) == 2


def test_a_failed_price_check_is_retried_within_the_window(conn, broker, executor):
    _hold(conn, broker, last="150")
    broker.fail("get_latest_trade")
    run_tick(conn, executor)
    assert _triggers(conn) == []
    run_tick(conn, executor, LATER)
    assert len(_triggers(conn)) == 1


def test_a_trade_stale_for_two_windows_is_an_error(conn, broker, executor, caplog):
    _hold(conn, broker, last="150")
    broker.set_trade("AAPL", "150", at=NOW - timedelta(hours=1))
    with caplog.at_level(logging.ERROR):
        run_tick(conn, executor)
        assert "unprotected" not in caplog.text
        run_tick(conn, executor, NOW + timedelta(minutes=30))
    assert "AAPL has had no fresh trade for 2 windows" in caplog.text
    assert _triggers(conn) == []


def test_an_unevaluated_trigger_is_reported_after_five_minutes(conn, broker, executor, caplog):
    _hold(conn, broker, last="150")
    run_tick(conn, executor)
    assert run_tick(conn, executor, NOW + timedelta(minutes=4)).unevaluated_triggers == 0
    with caplog.at_level(logging.ERROR):
        report = run_tick(conn, executor, NOW + timedelta(minutes=6))
    assert report.unevaluated_triggers == 1
    assert "is the gate's trigger runner running?" in caplog.text
    assert len(_triggers(conn)) == 1


def test_a_broken_config_switches_the_monitor_off_loudly_but_exits_still_go(
    conn, broker, tmp_path, caplog
):
    seed_baseline(conn)
    _hold(conn, broker, last="100")
    executor = Executor(broker, conn, tmp_path / "missing.yaml", _allow_savepoints=True)
    from tests.integration.execution.conftest import approved_verdict, sell_order

    approved_verdict(conn, sell_order(qty=10))
    with caplog.at_level(logging.ERROR):
        run_tick(conn, executor)
    assert _triggers(conn) == []
    assert "STOP-LOSS MONITOR IS OFF" in caplog.text
    assert [r.qty for r in broker.submissions] == [10]


def test_nothing_is_checked_outside_market_hours(conn, broker, executor):
    _hold(conn, broker, last="100")
    run_tick(conn, executor, NOW - timedelta(hours=2))
    assert _triggers(conn) == [] and "get_latest_trade" not in broker.calls
