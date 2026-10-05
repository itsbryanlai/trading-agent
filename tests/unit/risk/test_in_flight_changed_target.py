"""Feature 009 US2: a changed target orders only the difference from the settled
holdings (ADR 0020, research I4, I5; SC-002, SC-002a, FR-005)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from tests.unit.risk.builders import buy, config, context, sell
from tests.unit.risk.test_properties import PROPERTY, buy_ready_contexts, decisions
from trading_agent.risk import rules
from trading_agent.risk.gate import evaluate

CONFIG = config()
HUNDRED = Decimal(100)


def test_us2_1_a_raised_target_buys_only_the_difference():
    # $7,000 target less $4,848 in flight = $2,152 = 10 shares at the $202 ceiling.
    ctx = context(
        cash=50000,
        in_flight_buy_qty=24,
        in_flight_buy_cost_symbol=4848,
        in_flight_buy_cost_all=4848,
    )
    order = evaluate(buy(target=7, quote=200), ctx, CONFIG).verdict.order
    assert order.qty == 10 and order.trims == ()


def test_a_buy_ignores_the_sells_in_flight():
    # Review finding 4 (research I4a): 20 held, 10 being sold. A sell that might not
    # fill makes no room, so $1,000 to the 5% target = 4 shares at $202, not 14.
    ctx = context(shares_held=20, avg_entry_price=190, in_flight_sell_qty=10)
    assert evaluate(buy(target=5, quote=200), ctx, CONFIG).verdict.order.qty == 4


def test_us2_2_nothing_held_yet_so_a_sell_has_no_position():
    ctx = context(in_flight_buy_qty=24, in_flight_buy_cost_symbol=4848, in_flight_buy_cost_all=4848)
    result = evaluate(sell(target=0), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.NO_POSITION


def test_us2_3_a_full_exit_sells_only_what_is_not_already_being_sold():
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_sell_qty=30)
    assert evaluate(sell(target=0), ctx, CONFIG).verdict.order.qty == 20


def test_an_in_flight_buy_does_not_let_a_full_exit_sell_unarrived_shares():
    # Research I5: 50 held, 24 being bought, exit: sell the 50 now, not 74.
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_buy_qty=24)
    assert evaluate(sell(target=0), ctx, CONFIG).verdict.order.qty == 50


def test_an_in_flight_buy_does_not_enlarge_a_partial_sell():
    # Review finding 2 (research I5): keep = ceil(1000 / 200) = 5 of the 50 held, so 45,
    # not the 50 an unarrived buy would have let it sell.
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_buy_qty=24)
    assert evaluate(sell(target=1), ctx, CONFIG).verdict.order.qty == 45


def test_a_partial_sell_counts_only_the_sells_in_flight():
    # Review finding 2 (research I5): available = 50 - 10 = 40, keep = 10, so 30. The
    # 24 being bought are ignored.
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_buy_qty=24, in_flight_sell_qty=10)
    assert evaluate(sell(target=2), ctx, CONFIG).verdict.order.qty == 30


def test_a_raised_target_with_a_sell_in_flight_contradicts_it():
    # Research I5: 50 held at $200, a sell to 2% in flight (40), a decision for 5%.
    # available = 10 ($2,000) is below the $5,000 target by more than a share, and the
    # 24 shares being bought don't count.
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_sell_qty=40, in_flight_buy_qty=24)
    result = evaluate(sell(target=5), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.DIRECTION_CONTRADICTS_TARGET


def test_review_case_100_held_50_being_bought_a_sell_to_10_percent_sells_50():
    # Finding 2: 10% of $100,000 at $200 keeps 50 of the 100 held; the 50 being bought
    # don't raise it to the 100 an unarrived buy would have let it sell.
    ctx = context(shares_held=100, avg_entry_price=190, in_flight_buy_qty=50)
    assert evaluate(sell(target=10), ctx, CONFIG).verdict.order.qty == 50


def test_review_case_20_held_20_being_sold_a_buy_to_5_percent_buys_4():
    # Finding 4: committed $4,000, so $1,000 / $202 = 4 shares (not 19).
    ctx = context(shares_held=20, avg_entry_price=190, in_flight_sell_qty=20)
    assert evaluate(buy(target=5, quote=200), ctx, CONFIG).verdict.order.qty == 4


@st.composite
def in_flight_contexts(draw):
    """A buy-ready context with arbitrary orders in flight, on this symbol and others."""
    ctx = draw(buy_ready_contexts())
    buy_qty = draw(st.integers(0, 5000))
    price = draw(st.decimals(1, 5000, places=2, allow_nan=False, allow_infinity=False))
    other_cost = draw(st.decimals(0, 1_000_000, places=2, allow_nan=False, allow_infinity=False))
    return dataclasses.replace(
        ctx,
        in_flight_buy_qty=Decimal(buy_qty),
        in_flight_buy_cost_symbol=buy_qty * price,
        in_flight_buy_cost_all=buy_qty * price + other_cost,
        in_flight_sell_qty=Decimal(draw(st.integers(0, 12_000))),
    )


@PROPERTY
@given(ctx=in_flight_contexts(), request=decisions)
def test_sc_002_an_approved_sell_never_exceeds_the_unsold_shares(ctx, request):
    verdict = evaluate(request, ctx, CONFIG).verdict
    if verdict.approved and verdict.order.side == "sell":
        assert verdict.order.qty <= ctx.shares_held - ctx.in_flight_sell_qty


@PROPERTY
@given(ctx=in_flight_contexts(), request=decisions)
def test_sc_002a_an_approved_buy_keeps_cash_and_position_limits_whatever_sells_are_in_flight(
    ctx, request
):
    verdict = evaluate(request, ctx, CONFIG).verdict
    if not (verdict.approved and verdict.order.side == "buy"):
        return
    order = verdict.order
    reserve = CONFIG.cash_reserve_pct / HUNDRED * ctx.equity
    assert ctx.cash - order.qty * order.limit_price - ctx.in_flight_buy_cost_all >= reserve
    ceiling_value = CONFIG.max_position_pct / HUNDRED * ctx.equity
    assert (
        ctx.shares_held + ctx.in_flight_buy_qty + order.qty
    ) * order.limit_price <= ceiling_value
