"""Feature 009 US3: exits, hard stops and every no-in-flight verdict are untouched
(ADR 0020; FR-004, FR-005; SC-003, SC-004)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from tests.unit.risk.builders import buy, config, context, sell, trigger
from tests.unit.risk.test_properties import (
    PROPERTY,
    buy_ready_contexts,
    contexts,
    decisions,
    triggers,
)
from trading_agent.risk import gate, rules
from trading_agent.risk.gate import evaluate
from trading_agent.risk.model import Verdict

CONFIG = config()
_HUNDRED = Decimal(100)
ZEROS = {
    "in_flight_buy_qty": Decimal(0),
    "in_flight_buy_cost_symbol": Decimal(0),
    "in_flight_sell_qty": Decimal(0),
    "in_flight_buy_cost_all": Decimal(0),
}


# ---- The oracle: `_sell` and `_buy` exactly as they were on main before feature 009. ----


def _old_sell(request, ctx) -> Verdict:
    held = ctx.shares_held
    if held <= 0:
        return Verdict.reject(rules.NO_POSITION)
    target = request.target_weight_pct / _HUNDRED
    if target == 0:
        return Verdict.approve(gate._market_sell(request.symbol, held, ctx, source="decision"))
    if ctx.equity is None:
        return Verdict.reject(rules.NO_ACCOUNT_SNAPSHOT_TODAY)

    quote, equity = request.quote, ctx.equity
    if target * equity > held * quote + quote:
        return Verdict.reject(rules.DIRECTION_CONTRADICTS_TARGET)
    keep = gate._ceil(target * equity / quote)
    qty = held - keep
    if qty < 1:
        return Verdict.reject(rules.TARGET_ALREADY_MET)
    return Verdict.approve(gate._market_sell(request.symbol, qty, ctx, source="decision"))


def _old_buy(request, ctx, config, crossed) -> Verdict:
    stop = gate._account_stop(ctx, config, crossed) or gate._universe_stop(ctx.reference, config)
    if stop:
        return Verdict.reject(stop)

    equity, cash, quote, held = ctx.equity, ctx.cash, request.quote, ctx.shares_held
    target = request.target_weight_pct / _HUNDRED
    ceiling = gate.price_ceiling(quote, config.max_buy_price_tolerance_pct)

    if target * equity < held * quote - quote:
        return Verdict.reject(rules.DIRECTION_CONTRADICTS_TARGET)
    wanted = gate._floor((target * equity - held * quote) / ceiling)
    if wanted < 1:
        return Verdict.reject(rules.TARGET_ALREADY_MET)

    position_room = gate._floor(config.max_position_pct / _HUNDRED * equity / ceiling) - held
    cash_room = gate._floor((cash - config.cash_reserve_pct / _HUNDRED * equity) / ceiling)

    if position_room < 1:
        return Verdict.reject(rules.MAX_POSITION_PCT)
    if cash_room < 1:
        return Verdict.reject(rules.CASH_RESERVE_PCT)

    trims = tuple(
        name
        for name, room in (
            (rules.MAX_POSITION_PCT, position_room),
            (rules.CASH_RESERVE_PCT, cash_room),
        )
        if room < wanted
    )
    return Verdict.approve(
        gate.ApprovedOrder(
            symbol=request.symbol,
            side="buy",
            qty=min(wanted, position_room, cash_room),
            order_type="limit",
            limit_price=ceiling,
            trading_day=ctx.trading_day,
            exposure="increase",
            source="decision",
            trims=trims,
        )
    )


def _old_decision_verdict(request, ctx, config) -> Verdict:
    crossed = gate._loss_line_crossed(ctx, config)
    if request.direction == "sell":
        return _old_sell(request, ctx)
    return _old_buy(request, ctx, config, crossed)


# ---- Properties ----

money = st.decimals(0, 10_000_000, places=2, allow_nan=False, allow_infinity=False)
quantities = st.decimals(0, 20_000, places=0, allow_nan=False, allow_infinity=False)
in_flight_values = st.fixed_dictionaries(
    {
        "in_flight_buy_qty": quantities,
        "in_flight_buy_cost_symbol": money,
        "in_flight_sell_qty": quantities,
        "in_flight_buy_cost_all": money,
    }
)
any_context = st.one_of(buy_ready_contexts(), contexts(market_open=True))


@PROPERTY
@given(ctx=any_context, request=decisions)
def test_sc_003_with_nothing_in_flight_every_verdict_equals_todays(ctx, request):
    explicit = dataclasses.replace(ctx, **ZEROS)
    expected = _old_decision_verdict(request, ctx, CONFIG)
    result = evaluate(request, ctx, CONFIG)
    assert result.verdict == expected or _gated_before_sizing(request, ctx)
    assert evaluate(request, explicit, CONFIG) == result
    assert result.record_halt == gate._loss_line_crossed(ctx, CONFIG)


def _gated_before_sizing(request, ctx) -> bool:
    """evaluate() rejects a closed market or a stale decision before `_buy`/`_sell`."""
    return (not ctx.market_open) or (ctx.now - request.quote_time > gate.MAX_DECISION_QUOTE_AGE)


@PROPERTY
@given(ctx=any_context, request=triggers, in_flight=in_flight_values)
def test_sc_004_stop_loss_verdicts_ignore_everything_in_flight(ctx, request, in_flight):
    assert evaluate(request, dataclasses.replace(ctx, **in_flight), CONFIG) == evaluate(
        request, ctx, CONFIG
    )


@PROPERTY
@given(ctx=any_context, request=decisions, in_flight=in_flight_values)
def test_halt_recording_ignores_everything_in_flight(ctx, request, in_flight):
    with_flight = evaluate(request, dataclasses.replace(ctx, **in_flight), CONFIG)
    assert with_flight.record_halt == evaluate(request, ctx, CONFIG).record_halt


# ---- Scenarios ----


def test_us3_1_a_breached_stop_loss_exits_everything_held_despite_a_buy_in_flight():
    ctx = context(shares_held=50, avg_entry_price=200, in_flight_buy_qty=30)
    result = evaluate(trigger(observed=150), ctx, CONFIG)
    assert result.verdict.order.qty == 50 and result.verdict.order.source == "stop_loss"


def test_us3_1_a_stop_loss_ignores_a_sell_in_flight_too():
    ctx = context(shares_held=50, avg_entry_price=200, in_flight_sell_qty=50)
    assert evaluate(trigger(observed=150), ctx, CONFIG).verdict.order.qty == 50


def test_us3_2_the_crossed_loss_line_still_halts_buys_with_a_buy_in_flight():
    ctx = context(
        equity=79000,
        cash=79000,
        in_flight_buy_qty=24,
        in_flight_buy_cost_symbol=4848,
        in_flight_buy_cost_all=4848,
    )
    result = evaluate(buy(target=5), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.DAILY_LOSS_HALT
    assert result.record_halt


def test_us3_3_other_symbols_in_flight_buys_never_touch_a_sell():
    with_cost = context(shares_held=50, avg_entry_price=190, in_flight_buy_cost_all=90000)
    plain = context(shares_held=50, avg_entry_price=190)
    assert evaluate(sell(target=2), with_cost, CONFIG) == evaluate(sell(target=2), plain, CONFIG)


def test_us3_3_other_symbols_in_flight_buys_only_move_the_cash_reserve_check():
    only_cash = context(in_flight_buy_cost_all=1000)
    plain = context()
    a = evaluate(buy(target=5), only_cash, CONFIG).verdict.order
    b = evaluate(buy(target=5), plain, CONFIG).verdict.order
    assert a == b  # $1,000 still leaves room for all 24 shares


def test_us3_4_an_in_flight_buy_on_another_symbol_reduces_the_cash_available():
    ctx = context(cash=25000, in_flight_buy_cost_all=4000)
    as_if = context(cash=21000)
    result = evaluate(buy(target=5), ctx, CONFIG)
    assert result.verdict == evaluate(buy(target=5), as_if, CONFIG).verdict
    assert result.verdict.order.qty == 4  # floor((21000 - 20000) / 202)
    assert result.verdict.order.trims == (rules.CASH_RESERVE_PCT,)


def test_f1_a_sell_in_flight_never_makes_room_under_the_position_ceiling():
    # 50 held at $200 (10%), 40 being sold, a buy to 5%. Review finding 4 (research I4a):
    # the sell is ignored, so the verdict is today's, direction_contradicts_target.
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_sell_qty=40)
    request = buy(target=5)
    assert (
        _old_buy(request, ctx, CONFIG, False).rejection_rule == rules.DIRECTION_CONTRADICTS_TARGET
    )
    result = evaluate(request, ctx, CONFIG)
    assert not result.verdict.approved
    assert result.verdict.rejection_rule == rules.DIRECTION_CONTRADICTS_TARGET


def test_i5a_an_in_flight_sell_larger_than_the_holding_sells_nothing_more():
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_sell_qty=60)
    for target in (0, 2):
        result = evaluate(sell(target=target), ctx, CONFIG)
        assert result.verdict.rejection_rule == rules.TARGET_ALREADY_MET


def test_i5a_an_in_flight_sell_larger_than_the_holding_buys_no_more_than_today():
    held = context(shares_held=30, avg_entry_price=190)
    over_sold = dataclasses.replace(held, in_flight_sell_qty=Decimal(40))
    request = buy(target=10)
    today = _old_buy(request, held, CONFIG, False).order
    now = evaluate(request, over_sold, CONFIG).verdict.order
    assert now.qty == today.qty == 9  # floor(8000 / 202) - 30
    assert now.trims == (rules.MAX_POSITION_PCT,)


# ---- Research I4, I9: in-flight orders only ever make a verdict stricter. ----


@PROPERTY
@given(ctx=any_context, request=decisions, in_flight=in_flight_values)
def test_no_verdict_is_looser_than_the_old_gates_whatever_is_in_flight(ctx, request, in_flight):
    loaded = dataclasses.replace(ctx, **in_flight)
    result = evaluate(request, loaded, CONFIG)
    verdict = result.verdict
    if not verdict.approved or _gated_before_sizing(request, loaded):
        return
    old = _old_decision_verdict(request, ctx, CONFIG)
    assert old.approved
    assert old.order.side == verdict.order.side
    assert verdict.order.qty <= old.order.qty
