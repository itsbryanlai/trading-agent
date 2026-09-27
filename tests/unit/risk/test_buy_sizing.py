"""US1: every buy sized inside the limits (target weight at the price ceiling), or rejected."""

from decimal import Decimal

import pytest

from tests.unit.risk.builders import TODAY, buy, config, context, sell
from trading_agent.risk import rules
from trading_agent.risk.gate import evaluate

CONFIG = config()


def test_fresh_buy_sized_at_the_price_ceiling():
    result = evaluate(buy(target=5, quote=200), context(), CONFIG)
    order = result.verdict.order
    assert result.verdict.approved
    assert order.qty == 24  # floor(5000 / 202); 25 at $202 would breach the 5% target
    assert order.limit_price == Decimal("202.00")
    assert order.order_type == "limit"
    assert order.side == "buy"
    assert order.exposure == "increase"
    assert order.source == "decision"
    assert order.trims == ()
    assert order.time_in_force == "day"


def test_buy_trimmed_to_the_position_ceiling():
    # 30 shares at $200 = 6%; target 10%; ceiling room = floor(8000/202) - 30 = 9
    result = evaluate(buy(target=10), context(shares_held=30, avg_entry_price=190), CONFIG)
    assert result.verdict.order.qty == 9
    assert result.verdict.order.trims == (rules.MAX_POSITION_PCT,)


def test_buy_rejected_when_already_at_the_ceiling():
    result = evaluate(buy(target=10), context(shares_held=40, avg_entry_price=190), CONFIG)
    assert result.verdict.rejection_rule == rules.MAX_POSITION_PCT


def test_buy_rejected_at_the_cash_floor():
    result = evaluate(buy(target=5), context(cash=20000), CONFIG)
    assert result.verdict.rejection_rule == rules.CASH_RESERVE_PCT


def test_buy_trimmed_to_the_cash_above_the_floor():
    result = evaluate(buy(target=5), context(cash=20808), CONFIG)
    assert result.verdict.order.qty == 4  # floor(808 / 202)
    assert result.verdict.order.trims == (rules.CASH_RESERVE_PCT,)


def test_sell_to_zero_sells_every_share_as_a_market_order():
    result = evaluate(sell(target=0), context(shares_held=50, avg_entry_price=180), CONFIG)
    order = result.verdict.order
    assert (order.side, order.qty, order.order_type) == ("sell", 50, "market")
    assert order.limit_price is None
    assert order.exposure == "decrease"


def test_sell_to_a_partial_target_keeps_the_rounded_up_share_count():
    # 50 held at $200 = 10%; target 2% keeps ceil(2000/200) = 10, sells 40
    result = evaluate(sell(target=2), context(shares_held=50, avg_entry_price=180), CONFIG)
    assert result.verdict.order.qty == 40


def test_sell_of_a_symbol_not_held_rejected():
    assert evaluate(sell(), context(), CONFIG).verdict.rejection_rule == rules.NO_POSITION


def test_target_already_met_produces_no_order():
    # 25 shares at $200 = exactly 5%
    result = evaluate(buy(target=5), context(shares_held=25, avg_entry_price=190), CONFIG)
    assert result.verdict.rejection_rule == rules.TARGET_ALREADY_MET


@pytest.mark.parametrize(
    ("request_", "held"),
    [(buy(target=2), 25), (sell(target=8), 25)],
    ids=["buy-below-holding", "sell-above-holding"],
)
def test_direction_contradicting_target_rejected(request_, held):
    result = evaluate(request_, context(shares_held=held, avg_entry_price=190), CONFIG)
    assert result.verdict.rejection_rule == rules.DIRECTION_CONTRADICTS_TARGET


def test_result_carries_trading_day_and_config_version():
    result = evaluate(buy(), context(), CONFIG)
    assert result.verdict.order.trading_day == TODAY
    assert result.trading_day == TODAY
    assert result.config_version == CONFIG.version


def test_limit_price_rounds_down_to_the_cent():
    # 187.25 * 1.01 = 189.1225 -> 189.12, never above the tolerance
    result = evaluate(buy(target=5, quote="187.25"), context(), CONFIG)
    assert result.verdict.order.limit_price == Decimal("189.12")
