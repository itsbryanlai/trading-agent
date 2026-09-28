"""US3: the daily-loss halt, detected on every evaluation, and baseline selection."""

from decimal import Decimal

import pytest

from tests.unit.risk.builders import buy, config, context, sell
from trading_agent.risk import rules
from trading_agent.risk.gate import choose_baseline, evaluate

CONFIG = config()
HELD = {"shares_held": 50, "avg_entry_price": 180}


def test_buy_at_the_loss_line_is_rejected_and_records_the_halt():
    result = evaluate(buy(), context(baseline_equity=100000, equity=80000, cash=80000), CONFIG)
    assert result.verdict.rejection_rule == rules.DAILY_LOSS_HALT
    assert result.record_halt is True


def test_one_dollar_above_the_line_is_not_a_halt():
    result = evaluate(buy(), context(baseline_equity=100000, equity=80001, cash=80001), CONFIG)
    assert result.record_halt is False
    assert result.verdict.approved


def test_an_already_active_halt_is_not_recorded_again():
    result = evaluate(
        buy(), context(baseline_equity=100000, equity=70000, halt_active=True), CONFIG
    )
    assert result.verdict.rejection_rule == rules.DAILY_LOSS_HALT
    assert result.record_halt is False


def test_a_sell_at_the_line_is_approved_and_still_records_the_halt():
    ctx = context(baseline_equity=100000, equity=80000, cash=80000, **HELD)
    result = evaluate(sell(target=0), ctx, CONFIG)
    assert result.verdict.approved and result.verdict.order.qty == 50
    assert result.record_halt is True


@pytest.mark.parametrize(
    "overrides",
    [{"baseline_equity": None}, {"equity": None, "cash": None}],
    ids=["no-baseline", "no-equity"],
)
def test_no_halt_is_recorded_without_both_numbers(overrides):
    result = evaluate(sell(target=0), context(**overrides, **HELD), CONFIG)
    assert result.record_halt is False


def test_market_closed_records_no_halt():
    ctx = context(market_open=False, baseline_equity=100000, equity=50000)
    assert evaluate(buy(), ctx, CONFIG).record_halt is False


def test_stored_baseline_wins():
    assert choose_baseline(Decimal("99000"), Decimal("100000")) == (Decimal("99000"), False)


def test_pre_open_snapshot_used_and_marked_for_recording():
    assert choose_baseline(None, Decimal("100000")) == (Decimal("100000"), True)


def test_no_baseline_available():
    assert choose_baseline(None, None) == (None, False)


def test_a_crossing_earlier_today_records_the_halt_even_after_recovery():
    # ADR 0014 §3 (second review F7): the gate judges the lowest snapshot since
    # the open, not only the latest, just as Execution does.
    from decimal import Decimal

    from tests.unit.risk.builders import buy, config, context
    from trading_agent.risk import rules
    from trading_agent.risk.gate import evaluate

    ctx = context(
        equity=Decimal(95000), baseline_equity=Decimal(100000), lowest_equity_today=Decimal(79000)
    )
    result = evaluate(buy(), ctx, config())
    assert result.record_halt is True
    assert result.verdict.rejection_rule == rules.DAILY_LOSS_HALT
    above = context(
        equity=Decimal(95000), baseline_equity=Decimal(100000), lowest_equity_today=Decimal(80001)
    )
    assert evaluate(buy(), above, config()).record_halt is False
