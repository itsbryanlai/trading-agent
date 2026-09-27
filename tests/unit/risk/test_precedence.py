"""FR-016: when several rules apply, the verdict names the first in rejection-rules.md."""

import pytest

from tests.unit.risk.builders import buy, config, context, reference, sell
from trading_agent.risk import rules
from trading_agent.risk.gate import evaluate

CONFIG = config()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"trading_paused": True, "halt_active": True}, rules.TRADING_PAUSED),
        ({"equity": None, "cash": None, "baseline_equity": None}, rules.NO_ACCOUNT_SNAPSHOT_TODAY),
        ({"baseline_equity": None, "halt_active": True}, rules.NO_DAILY_BASELINE),
        ({"halt_active": True, "increase_orders_approved_today": 5}, rules.DAILY_LOSS_HALT),
        (
            {"increase_orders_approved_today": 5, "reference": reference(security_type="etf")},
            rules.DAILY_ORDER_CAP,
        ),
        (
            {"reference": reference(security_type="etf", share_price_usd="1")},
            rules.UNIVERSE_LISTING,
        ),
        (
            {"reference": reference(market_cap_usd="1", avg_daily_dollar_volume_usd="1")},
            rules.UNIVERSE_MARKET_CAP,
        ),
        (
            # universe failure beats a full position ceiling
            {
                "reference": reference(share_price_usd="1"),
                "shares_held": 40,
                "avg_entry_price": 190,
            },
            rules.UNIVERSE_SHARE_PRICE,
        ),
        ({"shares_held": 40, "avg_entry_price": 190, "cash": 20000}, rules.MAX_POSITION_PCT),
    ],
)
def test_buy_names_the_earliest_applicable_rule(overrides, expected):
    target = 10 if overrides.get("shares_held") else 5
    assert (
        evaluate(buy(target=target), context(**overrides), CONFIG).verdict.rejection_rule
        == expected
    )


def test_market_closed_beats_everything_for_both_sides():
    everything = {
        "market_open": False,
        "trading_paused": True,
        "halt_active": True,
        "equity": None,
        "cash": None,
        "reference": None,
    }
    assert (
        evaluate(buy(), context(**everything), CONFIG).verdict.rejection_rule == rules.MARKET_CLOSED
    )
    assert (
        evaluate(sell(), context(**everything), CONFIG).verdict.rejection_rule
        == rules.MARKET_CLOSED
    )


def test_exit_no_position_beats_missing_equity():
    result = evaluate(sell(target=2), context(equity=None, cash=None), CONFIG)
    assert result.verdict.rejection_rule == rules.NO_POSITION
