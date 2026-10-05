"""Feature 009: in-flight sells keep the documented order of the sell rules
(specs/002-risk-gate/contracts/rejection-rules.md: row 4 before row 6)."""

from tests.unit.risk.builders import config, context, sell
from trading_agent.risk import rules
from trading_agent.risk.gate import evaluate

CONFIG = config()


def test_partial_sell_with_no_snapshot_is_no_account_snapshot_even_if_all_is_being_sold():
    ctx = context(
        shares_held=50, avg_entry_price=190, in_flight_sell_qty=50, equity=None, cash=None
    )
    result = evaluate(sell(target=2), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.NO_ACCOUNT_SNAPSHOT_TODAY


def test_full_exit_with_every_share_already_being_sold_is_target_already_met():
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_sell_qty=50)
    result = evaluate(sell(target=0), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.TARGET_ALREADY_MET


def test_full_exit_needs_no_snapshot_with_a_sell_in_flight():
    ctx = context(
        shares_held=50, avg_entry_price=190, in_flight_sell_qty=30, equity=None, cash=None
    )
    result = evaluate(sell(target=0), ctx, CONFIG)
    assert result.verdict.order.qty == 20


def test_partial_sell_with_every_share_already_being_sold_is_target_already_met():
    # Not direction_contradicts_target, though settled holdings are 0 (research I5, I5a).
    ctx = context(shares_held=50, avg_entry_price=190, in_flight_sell_qty=60)
    result = evaluate(sell(target=2), ctx, CONFIG)
    assert result.verdict.rejection_rule == rules.TARGET_ALREADY_MET
