"""The adversarial review's order-path findings (research E16), each driven through
`tick` with the reviewer's own scenario."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.integration.execution.conftest import (
    approved_verdict,
    buy_order,
    outcomes,
    run_tick,
    seed_baseline,
    sell_order,
)
from tests.integration.storage.chain import order_id_for

# Monday 2026-09-28 closes at 20:00 UTC; Friday 2026-11-27 closes early, 18:00 UTC.
CLOSE = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
EARLY_CLOSE = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


def _quotes(broker, at, *symbols, ask="201.50"):
    for symbol in symbols:
        broker.set_quote(symbol, ask, at=at)


# --- H1: the clock is re-read just before submitting -------------------------


def test_a_tick_that_runs_past_the_close_submits_nothing(conn, broker, executor):
    seed_baseline(conn)
    start = CLOSE - timedelta(seconds=1)
    _quotes(broker, start, "AAPL")
    verdict = approved_verdict(conn, buy_order())

    report = run_tick(conn, executor, start, submit_at=CLOSE + timedelta(seconds=5))

    assert broker.submissions == [] and report.retried == 1
    assert outcomes(conn, verdict) == ([], [])  # retried, then lapses after the close


def test_no_submission_in_the_final_thirty_seconds(conn, broker, executor):
    seed_baseline(conn)
    start = CLOSE - timedelta(seconds=40)
    _quotes(broker, start, "AAPL")
    approved_verdict(conn, buy_order())

    run_tick(conn, executor, start, submit_at=CLOSE - timedelta(seconds=30))
    assert broker.submissions == []
    run_tick(conn, executor, start, submit_at=CLOSE - timedelta(seconds=31))
    assert len(broker.submissions) == 1


def test_the_window_follows_an_early_close(conn, broker, executor):
    seed_baseline_on(conn, datetime(2026, 11, 27, 13, 0, tzinfo=UTC))
    start = EARLY_CLOSE - timedelta(seconds=20)
    _quotes(broker, start, "AAPL")
    approved_verdict(conn, buy_order(day=start.date()))
    run_tick(conn, executor, start)
    assert broker.submissions == []


def seed_baseline_on(conn, taken_at):
    from tests.integration.risk.conftest import insert_snapshot

    insert_snapshot(conn, taken_at, "100000", "100000")


# --- H2: an unresolved placement holds back what it could affect ---------------


def test_a_timed_out_buy_holds_back_other_buys_until_it_is_counted(conn, broker, executor):
    # The reviewer's case: 100k equity, 30k cash, three buys of 24 x 201.50.
    seed_baseline(conn)
    broker.set_account(equity="100000", cash="30000")
    now = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    _quotes(broker, now, "AAPL", "MSFT", "NVDA")
    first = approved_verdict(conn, buy_order(symbol="AAPL"))
    second = approved_verdict(conn, buy_order(symbol="MSFT"))
    third = approved_verdict(conn, buy_order(symbol="NVDA"))
    broker.fail("submit_order", after_effect=True)

    run_tick(conn, executor, now)
    assert len(broker.submissions) == 1  # the other two waited

    later = now + timedelta(minutes=1)
    _quotes(broker, later, "AAPL", "MSFT", "NVDA")
    run_tick(conn, executor, later)

    # Whichever was processed first timed out and was recovered by the lookup; the
    # next fits (30,000 - 2 x 4,836 = 20,328 left); a third would leave 15,492.
    results = [outcomes(conn, v) for v in (first, second, third)]
    assert sum(len(orders) for orders, _ in results) == 2
    assert [r["reason"] for _, refusals in results for r in refusals] == ["cash_reserve_pct"]
    cost = sum(r.qty * r.limit_price for r in broker.submissions)
    assert Decimal(30000) - cost >= Decimal(20000)


def test_a_timed_out_buy_holds_back_a_second_buy_of_the_same_symbol(conn, broker, executor):
    seed_baseline(conn)
    now = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    _quotes(broker, now, "AAPL")
    verdicts = [approved_verdict(conn, buy_order()) for _ in range(2)]
    broker.fail("submit_order", after_effect=True)

    run_tick(conn, executor, now)
    later = now + timedelta(minutes=1)
    _quotes(broker, later, "AAPL")
    run_tick(conn, executor, later)

    # One placed (and recovered); the other refused: 48 x 201.50 = 9,672 > 8,000.
    refusals = [r["reason"] for v in verdicts for r in outcomes(conn, v)[1]]
    assert refusals == ["max_position_pct"]
    assert len(broker.submissions) == 1


def test_an_unresolved_sell_holds_back_exits_of_that_symbol_only(conn, broker, executor):
    broker.set_position("AAPL", 50, "200")
    broker.set_position("MSFT", 10, "400")
    now = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    aapl = [
        approved_verdict(conn, sell_order(qty=50)),
        approved_verdict(conn, sell_order(qty=50, source="stop_loss")),
    ]
    for verdict in aapl:
        broker.hide_from_lookup(order_id_for(verdict, "sell"))
    broker.fail("submit_order", after_effect=True)  # whichever AAPL sell goes first
    run_tick(conn, executor, now)
    # The other AAPL exit waits: it could oversell.
    assert [outcomes(conn, v) for v in aapl] == [([], []), ([], [])]

    other = approved_verdict(conn, sell_order(qty=10, symbol="MSFT"))
    run_tick(conn, executor, now + timedelta(seconds=30))  # still unresolved

    assert [outcomes(conn, v) for v in aapl] == [([], []), ([], [])]
    assert len(outcomes(conn, other)[0]) == 1  # MSFT is unaffected
    assert sorted(r.symbol for r in broker.submissions) == ["AAPL", "MSFT"]


# --- M1: lagging lookups can't turn a live order into a wrong outcome ----------


def test_a_timeout_just_before_the_close_is_not_expired_while_it_may_be_live(
    conn, broker, executor
):
    start = CLOSE - timedelta(seconds=40)
    broker.set_position("AAPL", 50, "200")
    verdict = approved_verdict(conn, sell_order(qty=50))
    client_id = order_id_for(verdict, "sell")
    broker.fail("submit_order", after_effect=True)
    broker.hide_from_lookup(client_id)
    run_tick(conn, executor, start)
    broker.fill(client_id, 50, "190")

    run_tick(conn, executor, CLOSE + timedelta(seconds=30))
    assert outcomes(conn, verdict) == ([], [])  # not "expired": it may be (and is) live

    broker.reveal(client_id)
    run_tick(conn, executor, CLOSE + timedelta(minutes=2, seconds=30))
    [order], refusals = outcomes(conn, verdict)
    assert refusals == [] and order["id"] == client_id
