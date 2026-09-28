"""US3 / SC-002: at most one broker order per approved verdict, whatever step a
crash interrupts. A "crash" here is an exception at that step, after which a
fresh Executor (no in-process memory) ticks again."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from tests.integration.execution.conftest import (
    NOW,
    REPO_CONFIG,
    approved_verdict,
    buy_order,
    outcomes,
    run_tick,
    seed_baseline,
    sell_order,
)
from tests.integration.storage.chain import order_id_for
from trading_agent.execution.broker import OrderRequest
from trading_agent.execution.service import Executor


def _fresh(conn, broker):
    return Executor(broker, conn, REPO_CONFIG, _allow_savepoints=True)


def _seed(conn, broker, side):
    seed_baseline(conn)
    if side == "sell":
        broker.set_position("AAPL", 50, "200")
        return approved_verdict(conn, sell_order(qty=50))
    return approved_verdict(conn, buy_order())


def _crash_record_once(monkeypatch, after_insert: bool):
    original = Executor._record_order
    state = {"crashed": False}

    def crashing(self, *args, **kwargs):
        if not state["crashed"]:
            state["crashed"] = True
            if after_insert:
                original(self, *args, **kwargs)
            raise RuntimeError("simulated crash")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Executor, "_record_order", crashing)


CRASH_POINTS = [
    "before_find_order",
    "before_submit",
    "submit_timed_out_after_placing",
    "after_submit_before_insert",
    "after_insert_before_commit",
]


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("crash", CRASH_POINTS)
def test_one_order_per_verdict_whatever_step_crashes(conn, broker, monkeypatch, side, crash):
    verdict = _seed(conn, broker, side)
    if crash == "before_find_order":
        broker.fail("find_order")
    elif crash == "before_submit":
        broker.fail("get_latest_quote" if side == "buy" else "get_positions")
    elif crash == "submit_timed_out_after_placing":
        broker.fail("submit_order", after_effect=True)
    else:
        _crash_record_once(monkeypatch, after_insert=crash == "after_insert_before_commit")

    run_tick(conn, _fresh(conn, broker))
    run_tick(conn, _fresh(conn, broker), NOW + timedelta(minutes=1))
    run_tick(conn, _fresh(conn, broker), NOW + timedelta(minutes=2))

    client_id = order_id_for(verdict, side)
    assert len(broker.orders_for(client_id)) == 1
    orders, refusals = outcomes(conn, verdict)
    assert refusals == [] and [o["id"] for o in orders] == [client_id]
    assert orders[0]["broker_order_id"] == broker.orders_for(client_id)[0].broker_order_id


def test_an_identifier_clash_is_refused_without_touching_the_other_order(conn, broker, executor):
    seed_baseline(conn)
    first = approved_verdict(
        conn, buy_order(), verdict_id=UUID("3f9c2a1b-0000-4000-8000-000000000001")
    )
    second = approved_verdict(
        conn, buy_order(), verdict_id=UUID("3f9c2a1b-ffff-4000-8000-000000000002")
    )
    run_tick(conn, executor)  # the first one's order now exists in both places

    orders_first, _ = outcomes(conn, first)
    _, [refusal] = outcomes(conn, second)
    assert len(orders_first) == 1 and len(broker.submissions) == 1
    assert refusal["reason"] == "identifier_clash"
    assert refusal["details"]["other_verdict_id"] == str(first)


def test_a_crash_just_before_the_close_is_recorded_as_the_order_after_it(conn, broker):
    verdict = _seed(conn, broker, "sell")
    broker.fail("submit_order", after_effect=True)
    before_close = datetime(2026, 9, 28, 19, 59, tzinfo=UTC)
    run_tick(conn, _fresh(conn, broker), before_close)

    run_tick(conn, _fresh(conn, broker), datetime(2026, 9, 28, 20, 1, tzinfo=UTC))

    orders, refusals = outcomes(conn, verdict)
    assert refusals == [] and len(orders) == 1 and len(broker.submissions) == 1


def test_a_lagging_lookup_after_a_timeout_never_leads_to_a_hidden_second_order(
    conn, broker, executor
):
    verdict = _seed(conn, broker, "buy")
    client_id = order_id_for(verdict)
    broker.reject_duplicate_client_ids = True
    broker.fail("submit_order", after_effect=True)
    broker.hide_from_lookup(client_id)

    run_tick(conn, executor)  # timed out; placed but invisible to the lookup
    run_tick(conn, executor, NOW + timedelta(minutes=1))  # still inside the wait: no resubmit
    assert len(broker.calls_named("submit_order")) == 1

    later = NOW + timedelta(minutes=3)
    broker.set_quote("AAPL", "201.50", at=later)
    run_tick(conn, executor, later)  # resubmits; the broker rejects the duplicate id
    assert len(broker.calls_named("submit_order")) == 2
    # Still lagging: the rejection is of a duplicate of a live order, so nothing
    # is recorded, least of all a phantom "rejected" (research E16).
    assert outcomes(conn, verdict) == ([], [])

    broker.reveal(client_id)  # the lookup catches up
    run_tick(conn, executor, later + timedelta(minutes=1))

    [order], _ = outcomes(conn, verdict)
    assert order["status"] == "submitted"
    assert order["broker_order_id"] == broker.orders_for(client_id)[0].broker_order_id
    assert len(broker.orders_for(client_id)) == 1


def test_a_lookup_that_caught_up_within_the_wait_means_one_submission(conn, broker, executor):
    verdict = _seed(conn, broker, "buy")
    client_id = order_id_for(verdict)
    broker.fail("submit_order", after_effect=True)
    broker.hide_from_lookup(client_id)
    run_tick(conn, executor)
    broker.reveal(client_id)
    later = NOW + timedelta(minutes=3)
    broker.set_quote("AAPL", "201.50", at=later)
    run_tick(conn, executor, later)
    assert len(broker.orders_for(client_id)) == 1
    assert len(outcomes(conn, verdict)[0]) == 1


def test_a_recovered_buy_keeps_its_limit_price_and_counts_toward_open_cost(conn, broker):
    seed_baseline(conn)
    broker.set_account(equity="100000", cash="30000")
    first = approved_verdict(conn, buy_order())
    broker.fail("submit_order", after_effect=True)
    run_tick(conn, _fresh(conn, broker))
    run_tick(conn, _fresh(conn, broker), NOW + timedelta(seconds=30))  # recovered

    [order], _ = outcomes(conn, first)
    assert order["limit_price"] == Decimal("201.5")

    # 30,000 - 24 x 201.50 open - 26 x 200 = 19,964 < 20,000: refused only if counted
    broker.set_quote("MSFT", "200", at=NOW + timedelta(seconds=60))
    second = approved_verdict(conn, buy_order(qty=26, ceiling="202", symbol="MSFT"))
    run_tick(conn, _fresh(conn, broker), NOW + timedelta(seconds=60))
    _, [refusal] = outcomes(conn, second)
    assert refusal["reason"] == "cash_reserve_pct"


def test_the_fake_placed_exactly_what_was_requested(broker):
    # Guard for the tests above: submissions and the order book agree.
    request = OrderRequest("2026-09-28-AAPL-buy-00000000", "AAPL", "buy", 1, "limit", Decimal(1))
    broker.submit_order(request)
    assert broker.submissions == [request]
