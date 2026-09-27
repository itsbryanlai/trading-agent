"""US2: hard stops block new exposure but never block an exit (market closed aside)."""

import pytest

from tests.unit.risk.builders import buy, config, context, reference, sell
from trading_agent.risk import rules
from trading_agent.risk.gate import evaluate

CONFIG = config()
HELD = {"shares_held": 50, "avg_entry_price": 180}


def _rule(request, ctx):
    return evaluate(request, ctx, CONFIG).verdict.rejection_rule


def test_market_closed_rejects_both_sides():
    assert _rule(buy(), context(market_open=False)) == rules.MARKET_CLOSED
    assert _rule(sell(), context(market_open=False, **HELD)) == rules.MARKET_CLOSED


@pytest.mark.parametrize(
    ("overrides", "rule"),
    [
        ({"trading_paused": True}, rules.TRADING_PAUSED),
        ({"equity": None, "cash": None}, rules.NO_ACCOUNT_SNAPSHOT_TODAY),
        ({"baseline_equity": None}, rules.NO_DAILY_BASELINE),
        ({"halt_active": True}, rules.DAILY_LOSS_HALT),
        ({"increase_orders_approved_today": 5}, rules.DAILY_ORDER_CAP),
        ({"increase_orders_approved_today": 9}, rules.DAILY_ORDER_CAP),
        ({"reference": None}, rules.UNIVERSE_NO_REFERENCE_DATA),
        ({"reference": reference(security_type="etf")}, rules.UNIVERSE_LISTING),
        ({"reference": reference(exchange_mic="OTCM")}, rules.UNIVERSE_LISTING),
        ({"reference": reference(market_cap_usd="499999999.99")}, rules.UNIVERSE_MARKET_CAP),
        (
            {"reference": reference(avg_daily_dollar_volume_usd="9999999")},
            rules.UNIVERSE_DOLLAR_VOLUME,
        ),
        ({"reference": reference(share_price_usd="4.99")}, rules.UNIVERSE_SHARE_PRICE),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_each_stop_rejects_a_buy_and_approves_a_full_sell(overrides, rule):
    assert _rule(buy(), context(**overrides)) == rule
    exit_ = evaluate(sell(target=0), context(**{**overrides, **HELD}), CONFIG)
    assert exit_.verdict.approved and exit_.verdict.order.qty == 50


def test_approved_sell_does_not_need_room_under_the_cap():
    result = evaluate(sell(target=0), context(increase_orders_approved_today=5, **HELD), CONFIG)
    assert result.verdict.order.exposure == "decrease"


def test_missing_equity_blocks_a_partial_sell_but_not_a_full_one():
    no_equity = {"equity": None, "cash": None, **HELD}
    assert _rule(sell(target=2), context(**no_equity)) == rules.NO_ACCOUNT_SNAPSHOT_TODAY
    assert evaluate(sell(target=0), context(**no_equity), CONFIG).verdict.approved


def test_universe_floors_are_inclusive():
    at_floor = reference(
        market_cap_usd="500000000",
        avg_daily_dollar_volume_usd="10000000",
        share_price_usd="5",
    )
    assert evaluate(buy(quote=5, target=1), context(reference=at_floor), CONFIG).verdict.approved


@pytest.mark.parametrize("mic", ["XNYS", "XNAS", "XASE"])
def test_each_us_listed_exchange_passes(mic):
    assert evaluate(buy(), context(reference=reference(exchange_mic=mic)), CONFIG).verdict.approved


def test_adr_rejected_as_not_common_equity():
    assert _rule(buy(), context(reference=reference(security_type="adr"))) == rules.UNIVERSE_LISTING
