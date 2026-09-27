"""US3 / E4: every approval whose trading day is over ends with exactly one
outcome, and a lapsed approval never reaches the broker."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tests.integration.execution.conftest import (
    approved_verdict,
    buy_order,
    outcomes,
    run_tick,
    seed_baseline,
    sell_order,
)
from tests.integration.storage.chain import insert_refusal, order_id_for
from trading_agent.execution.broker import OrderRequest

FRIDAY = date(2026, 9, 25)
AFTER_CLOSE = datetime(2026, 9, 28, 20, 1, tzinfo=UTC)


def test_yesterdays_unsubmitted_approvals_expire_and_never_reach_the_broker(conn, broker, executor):
    buy = approved_verdict(conn, buy_order(day=FRIDAY))
    sell = approved_verdict(conn, sell_order(day=FRIDAY))
    broker.set_position("AAPL", 50, "200")

    run_tick(conn, executor)

    for verdict in (buy, sell):
        orders, [refusal] = outcomes(conn, verdict)
        assert orders == [] and refusal["reason"] == "approval_expired"
        assert refusal["details"]["trading_day"] == "2026-09-25"
    assert broker.submissions == []


def test_todays_unsubmitted_approval_expires_after_the_close(conn, broker, executor):
    verdict = approved_verdict(conn, sell_order())
    run_tick(conn, executor, AFTER_CLOSE)
    _, [refusal] = outcomes(conn, verdict)
    assert refusal["reason"] == "approval_expired"
    assert broker.submissions == []


def test_an_approval_that_already_has_an_outcome_gets_nothing_new(conn, broker, executor):
    verdict = approved_verdict(conn, sell_order(day=FRIDAY))
    insert_refusal(conn, verdict, "trading_paused")
    run_tick(conn, executor)
    _, refusals = outcomes(conn, verdict)
    assert [r["reason"] for r in refusals] == ["trading_paused"]


def test_an_order_the_broker_has_is_recorded_rather_than_refused(conn, broker, executor):
    verdict = approved_verdict(conn, sell_order(day=FRIDAY))
    client_id = order_id_for(verdict, "sell", day="2026-09-25")
    broker.submit_order(OrderRequest(client_id, "AAPL", "sell", 50, "market"))

    run_tick(conn, executor)

    [order], refusals = outcomes(conn, verdict)
    assert refusals == [] and order["id"] == client_id


def test_with_the_broker_unreachable_the_sweep_waits(conn, broker, executor):
    verdict = approved_verdict(conn, sell_order(day=FRIDAY))
    broker.fail("find_order")
    run_tick(conn, executor)
    assert outcomes(conn, verdict) == ([], [])
    run_tick(conn, executor)
    _, [refusal] = outcomes(conn, verdict)
    assert refusal["reason"] == "approval_expired"


def test_every_past_approval_has_exactly_one_outcome_after_a_tick(conn, broker, executor):
    seed_baseline(conn)
    verdicts = [approved_verdict(conn, buy_order(day=FRIDAY)) for _ in range(3)]
    verdicts.append(approved_verdict(conn, sell_order(day=FRIDAY)))
    insert_refusal(conn, verdicts[0], "trading_paused")
    run_tick(conn, executor)
    for verdict in verdicts:
        orders, refusals = outcomes(conn, verdict)
        assert len(orders) + len(refusals) == 1
