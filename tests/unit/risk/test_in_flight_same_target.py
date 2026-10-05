"""Feature 009 US1: deciding the same target again while its order is in flight
orders nothing more (ADR 0020, research I4 and I5; SC-001)."""

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


def test_us1_1_an_unplaced_buy_in_flight_meets_the_target():
    # 24 shares at the $202 ceiling = $4,848 of the $5,000 target; $152 is under one share.
    ctx = context(in_flight_buy_qty=24, in_flight_buy_cost_symbol=4848, in_flight_buy_cost_all=4848)
    result = evaluate(buy(target=5, quote=200), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.TARGET_ALREADY_MET


def test_us1_2_a_partly_filled_buy_meets_the_target():
    # 10 held (positions already include the fill) and 14 left in flight at $202.
    ctx = context(
        shares_held=10,
        avg_entry_price=202,
        in_flight_buy_qty=14,
        in_flight_buy_cost_symbol=14 * 202,
        in_flight_buy_cost_all=14 * 202,
    )
    result = evaluate(buy(target=5, quote=200), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.TARGET_ALREADY_MET


def test_us1_3_an_in_flight_sell_meets_the_target():
    # 50 held at $200; a sell of 40 in flight leaves 10 = 2% of $100,000.
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_sell_qty=40)
    result = evaluate(sell(target=2, quote=200), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.TARGET_ALREADY_MET


def test_low_price_buy_is_sized_once_then_not_again():
    # Equity $100,000, quote $10, 1% tolerance: ceiling $10.10, 495 shares = $4,999.50.
    first = evaluate(buy(target=5, quote=10), context(), CONFIG)
    assert first.verdict.order.qty == 495
    cost = Decimal(495) * Decimal("10.10")
    ctx = context(
        in_flight_buy_qty=495, in_flight_buy_cost_symbol=cost, in_flight_buy_cost_all=cost
    )
    again = evaluate(buy(target=5, quote=10), ctx, CONFIG)
    assert again.verdict.rejection_rule == rules.TARGET_ALREADY_MET


@PROPERTY
@given(
    ctx=buy_ready_contexts(),
    request=decisions,
    tolerance=st.decimals(0, 5, places=2, allow_nan=False, allow_infinity=False),
)
def test_sc_001_an_approval_added_in_flight_is_not_approved_again(ctx, request, tolerance):
    cfg = config(max_buy_price_tolerance_pct=tolerance)
    first = evaluate(request, ctx, cfg).verdict
    if not first.approved:
        return
    order = first.order
    if order.side == "buy":
        cost = order.qty * order.limit_price
        after = dataclasses.replace(
            ctx,
            in_flight_buy_qty=ctx.in_flight_buy_qty + order.qty,
            in_flight_buy_cost_symbol=ctx.in_flight_buy_cost_symbol + cost,
            in_flight_buy_cost_all=ctx.in_flight_buy_cost_all + cost,
        )
    else:
        after = dataclasses.replace(ctx, in_flight_sell_qty=ctx.in_flight_sell_qty + order.qty)
    assert not evaluate(request, after, cfg).verdict.approved
