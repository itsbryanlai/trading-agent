"""US1 through the service: approved buys reach the fake broker only when live
numbers confirm them, and every approval ends with one recorded outcome."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tests.integration.execution.conftest import (
    NOW,
    approved_verdict,
    buy_order,
    insert_snapshot,
    outcomes,
    run_tick,
    seed_baseline,
    sell_order,
    set_paused,
)
from tests.integration.risk.conftest import insert_position


def test_an_approved_buy_becomes_one_limit_order_at_the_ask(conn, broker, executor):
    seed_baseline(conn)
    verdict = approved_verdict(conn, buy_order())

    report = run_tick(conn, executor)

    orders, refusals = outcomes(conn, verdict)
    assert refusals == [] and len(orders) == 1
    order = orders[0]
    assert order["id"] == f"2026-09-28-AAPL-buy-{str(verdict)[:8]}"
    assert order["status"] == "submitted"
    assert order["limit_price"] == Decimal("201.5")
    assert order["broker_order_id"] == "broker-1"
    [request] = broker.submissions
    assert (request.client_order_id, request.qty, request.limit_price) == (
        order["id"],
        24,
        Decimal("201.50"),
    )
    assert report.submitted == 1


def test_the_live_account_is_recorded_before_the_buy_is_submitted(conn, broker, executor):
    seed_baseline(conn)
    broker.set_account(equity="99000", cash="98000", buying_power="196000")
    approved_verdict(conn, buy_order())

    run_tick(conn, executor)

    snapshot = conn.execute(
        "SELECT * FROM account_snapshots WHERE taken_at = %s", (NOW,)
    ).fetchone()
    assert (snapshot["equity"], snapshot["cash"], snapshot["buying_power"]) == (
        Decimal("99000.00"),
        Decimal("98000.00"),
        Decimal("196000.00"),
    )
    assert broker.calls.index("get_account") < broker.calls.index("submit_order")


def test_ask_above_the_ceiling_is_refused_and_nothing_is_sent(conn, broker, executor):
    seed_baseline(conn)
    broker.set_quote("AAPL", "202.01")
    verdict = approved_verdict(conn, buy_order())

    run_tick(conn, executor)

    orders, [refusal] = outcomes(conn, verdict)
    assert orders == [] and broker.submissions == []
    assert refusal["reason"] == "quote_above_ceiling"
    assert refusal["details"]["ask"] == "202.01"


def test_a_crossing_earlier_today_refuses_a_buy_after_equity_recovered(conn, broker, executor):
    seed_baseline(conn)
    insert_snapshot(conn, datetime(2026, 9, 28, 13, 50, tzinfo=UTC), "79000", "79000")
    broker.set_account(equity="95000", cash="95000")
    verdict = approved_verdict(conn, buy_order())

    run_tick(conn, executor)

    _, [refusal] = outcomes(conn, verdict)
    assert refusal["reason"] == "daily_loss_line_crossed"
    assert broker.submissions == []
    # The refusal names the snapshots it judged by (contracts/refusal-reasons.md).
    details = refusal["details"]
    low = conn.execute("SELECT id FROM account_snapshots WHERE equity = 79000").fetchone()["id"]
    just_taken = conn.execute(
        "SELECT id FROM account_snapshots WHERE taken_at = %s", (NOW,)
    ).fetchone()["id"]
    assert details["min_snapshot_id"] == str(low)
    assert details["snapshot_id"] == str(just_taken)
    assert details["min_equity_since_open"] == "79000.00"


def test_a_retried_buy_keeps_nothing_not_even_the_pre_buy_snapshot(conn, broker, executor):
    # T061: an unusable quote retries every minute; it must not write a snapshot each time.
    from datetime import timedelta

    seed_baseline(conn)
    broker.set_quote("AAPL", "201.50", at=NOW - timedelta(minutes=5))  # stale
    verdict = approved_verdict(conn, buy_order())
    before = conn.execute("SELECT count(*) AS n FROM account_snapshots").fetchone()["n"]

    report = run_tick(conn, executor)

    assert report.retried == 1 and "get_account" in broker.calls
    assert conn.execute("SELECT count(*) AS n FROM account_snapshots").fetchone()["n"] == before
    assert outcomes(conn, verdict) == ([], [])


def test_paused_refuses_buys_but_still_sends_sells(conn, broker, executor):
    seed_baseline(conn)
    set_paused(conn)
    insert_position(conn, qty=50, avg_entry="200")
    broker.set_position("AAPL", 50, "200")
    buy = approved_verdict(conn, buy_order())
    sell = approved_verdict(conn, sell_order())

    run_tick(conn, executor)

    _, [refusal] = outcomes(conn, buy)
    assert refusal["reason"] == "trading_paused"
    [sell_order_row], _ = outcomes(conn, sell)
    assert [r.side for r in broker.submissions] == ["sell"]
    assert sell_order_row["limit_price"] is None


def test_a_second_tick_changes_nothing(conn, broker, executor):
    seed_baseline(conn)
    verdict = approved_verdict(conn, buy_order())
    run_tick(conn, executor)
    report = run_tick(conn, executor)
    orders, refusals = outcomes(conn, verdict)
    assert len(orders) == 1 and refusals == [] and len(broker.submissions) == 1
    assert report.submitted == 0


def test_a_rejected_verdict_is_never_touched(conn, broker, executor):
    seed_baseline(conn)
    verdict = approved_verdict(conn, buy_order(), approved=False)
    run_tick(conn, executor)
    assert outcomes(conn, verdict) == ([], [])
    assert broker.submissions == []


def test_a_transient_failure_records_nothing_and_the_next_tick_submits(conn, broker, executor):
    seed_baseline(conn)
    verdict = approved_verdict(conn, buy_order())
    broker.fail("get_latest_ask")

    report = run_tick(conn, executor)
    assert outcomes(conn, verdict) == ([], []) and report.retried == 1

    run_tick(conn, executor)
    orders, _ = outcomes(conn, verdict)
    assert len(orders) == 1 and len(broker.submissions) == 1


def test_a_broker_rejection_is_recorded_on_the_order_and_not_retried(conn, broker, executor):
    seed_baseline(conn)
    broker.reject_next("insufficient buying power")
    verdict = approved_verdict(conn, buy_order())

    run_tick(conn, executor)
    run_tick(conn, executor)

    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "rejected" and order["broker_order_id"] is None
    assert order["broker_reason"] == "insufficient buying power"
    assert order["limit_price"] == Decimal("201.5")
    assert broker.submissions == []


def test_no_baseline_refuses_the_buy(conn, broker, executor):
    verdict = approved_verdict(conn, buy_order())
    run_tick(conn, executor)
    _, [refusal] = outcomes(conn, verdict)
    assert refusal["reason"] == "no_daily_baseline"
    assert "get_account" not in broker.calls


def test_an_invalid_symbol_is_refused_before_any_broker_call(conn, broker, executor):
    seed_baseline(conn)
    verdict = approved_verdict(conn, buy_order(symbol="BRK-B"))
    run_tick(conn, executor)
    _, [refusal] = outcomes(conn, verdict)
    assert refusal["reason"] == "invalid_symbol"
    assert "find_order" not in broker.calls and broker.submissions == []
