"""US2 through the service: exits go out under every stop, only for shares held."""

from __future__ import annotations

from tests.integration.execution.conftest import (
    approved_verdict,
    outcomes,
    run_tick,
    seed_baseline,
    sell_order,
    set_paused,
)
from tests.integration.risk.conftest import insert_position
from trading_agent.execution.service import Executor


def test_a_decision_sell_and_a_stop_loss_exit_on_one_day_get_distinct_orders(
    conn, broker, executor
):
    insert_position(conn, qty=80, avg_entry="200")
    broker.set_position("AAPL", 80, "200")
    decision_sell = approved_verdict(conn, sell_order(qty=30))
    stop_exit = approved_verdict(conn, sell_order(qty=50, source="stop_loss"))

    run_tick(conn, executor)

    [a], _ = outcomes(conn, decision_sell)
    [b], _ = outcomes(conn, stop_exit)
    assert a["id"] != b["id"]  # ADR 0012: no clash on symbol, side and day
    assert sorted(r.qty for r in broker.submissions) == [30, 50]
    assert {r.order_type for r in broker.submissions} == {"market"}


def test_an_exit_goes_out_under_the_loss_line_the_pause_and_a_broken_config(conn, broker, tmp_path):
    seed_baseline(conn)
    set_paused(conn)
    broker.set_account(equity="70000", cash="10000")
    insert_position(conn, qty=50, avg_entry="200")
    broker.set_position("AAPL", 50, "200")
    verdict = approved_verdict(conn, sell_order(qty=50))
    executor = Executor(broker, conn, tmp_path / "missing.yaml", _allow_savepoints=True)

    run_tick(conn, executor)

    [order], refusals = outcomes(conn, verdict)
    assert refusals == [] and order["status"] == "submitted"
    assert [r.qty for r in broker.submissions] == [50]


def test_no_account_snapshot_is_taken_for_an_exit(conn, broker, executor):
    insert_position(conn, qty=50, avg_entry="200")
    broker.set_position("AAPL", 50, "200")
    approved_verdict(conn, sell_order(qty=50))
    before = conn.execute("SELECT count(*) AS n FROM account_snapshots").fetchone()["n"]

    run_tick(conn, executor)

    after = conn.execute("SELECT count(*) AS n FROM account_snapshots").fetchone()["n"]
    assert after == before and "get_account" not in broker.calls


def test_selling_more_than_the_broker_holds_is_refused(conn, broker, executor):
    broker.set_position("AAPL", 30, "200")
    verdict = approved_verdict(conn, sell_order(qty=50))

    run_tick(conn, executor)

    _, [refusal] = outcomes(conn, verdict)
    assert refusal["reason"] == "shares_held_differ"
    assert refusal["details"] == {"held": "30", "open_sell_qty": "0", "qty": 50}
    assert broker.submissions == []


def test_exits_are_processed_before_buys_in_a_tick(conn, broker, executor):
    from tests.integration.execution.conftest import buy_order

    seed_baseline(conn)
    broker.set_position("MSFT", 10, "400")
    approved_verdict(conn, buy_order())  # recorded first
    approved_verdict(conn, sell_order(qty=10, symbol="MSFT"))

    run_tick(conn, executor)

    assert [r.side for r in broker.submissions] == ["sell", "buy"]
