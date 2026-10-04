"""ADR 0019: the gate rejects a buy or sell decision whose quote is over 15 minutes old.

Exactly 15 minutes is usable; one second more is not. A stale decision still records
the daily-loss halt, and a stop-loss trigger is unaffected.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.unit.risk.builders import NOW, buy, config, context, sell, trigger
from trading_agent.risk import rules
from trading_agent.risk.gate import MAX_DECISION_QUOTE_AGE, evaluate

CFG = config()
AT_LIMIT = NOW - timedelta(minutes=15)
OVER_LIMIT = NOW - timedelta(minutes=15, seconds=1)


def test_the_limit_is_fifteen_minutes():
    assert MAX_DECISION_QUOTE_AGE == timedelta(minutes=15)


def test_a_buy_on_a_quote_exactly_fifteen_minutes_old_is_not_stale():
    assert evaluate(buy(quote_time=AT_LIMIT), context(), CFG).verdict.approved


def test_a_buy_on_a_quote_a_second_past_fifteen_minutes_is_stale():
    verdict = evaluate(buy(quote_time=OVER_LIMIT), context(), CFG).verdict
    assert verdict.rejection_rule == rules.DECISION_STALE


@pytest.mark.parametrize("target", [0, 2])
def test_a_sell_is_stale_at_the_same_boundary(target):
    ctx = context(shares_held=50, avg_entry_price=200)
    assert evaluate(sell(target=target, quote_time=AT_LIMIT), ctx, CFG).verdict.approved
    stale = evaluate(sell(target=target, quote_time=OVER_LIMIT), ctx, CFG).verdict
    assert stale.rejection_rule == rules.DECISION_STALE


def test_a_stale_sell_with_no_position_is_stale_not_no_position():
    verdict = evaluate(sell(quote_time=OVER_LIMIT), context(), CFG).verdict
    assert verdict.rejection_rule == rules.DECISION_STALE


def test_a_stale_buy_is_stale_before_every_buy_rule():
    ctx = context(trading_paused=True, equity=None, cash=None, reference=None)
    verdict = evaluate(buy(quote_time=OVER_LIMIT), ctx, CFG).verdict
    assert verdict.rejection_rule == rules.DECISION_STALE


def test_market_closed_still_comes_first():
    ctx = context(market_open=False)
    verdict = evaluate(buy(quote_time=OVER_LIMIT), ctx, CFG).verdict
    assert verdict.rejection_rule == rules.MARKET_CLOSED


@pytest.mark.parametrize("make", [buy, sell])
def test_a_stale_decision_still_records_the_daily_loss_halt(make):
    ctx = context(equity=70000, baseline_equity=100000, shares_held=50, avg_entry_price=200)
    result = evaluate(make(quote_time=OVER_LIMIT), ctx, CFG)
    assert result.verdict.rejection_rule == rules.DECISION_STALE
    assert result.record_halt is True


def test_a_stale_decision_with_equity_above_the_line_records_no_halt():
    result = evaluate(buy(quote_time=OVER_LIMIT), context(), CFG)
    assert result.record_halt is False


def test_a_stop_loss_trigger_is_not_subject_to_the_decision_limit():
    ctx = context(shares_held=50, avg_entry_price=200)
    # A trigger keeps its own 10-minute rule and rule name, whatever the decision limit is.
    fresh = trigger(observed=160, observed_at=NOW - timedelta(minutes=9))
    assert evaluate(fresh, ctx, CFG).verdict.approved
    stale = trigger(observed=160, observed_at=NOW - timedelta(minutes=11))
    assert evaluate(stale, ctx, CFG).verdict.rejection_rule == rules.STOP_LOSS_TRIGGER_STALE


def test_a_fresh_decision_is_judged_as_before():
    verdict = evaluate(buy(quote_time=NOW), context(), CFG).verdict
    assert verdict.approved and verdict.order.qty > 0
