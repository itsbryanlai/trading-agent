"""US4: a stop-loss trigger is confirmed against the position's own entry price
and approved as a full market exit that no halt, pause or cap can block."""

from tests.unit.risk.builders import D, config, context, trigger
from trading_agent.risk import rules
from trading_agent.risk.gate import evaluate

CONFIG = config()  # stop_loss_pct 20: line for a $200 entry is $160
HELD = {"shares_held": 50, "avg_entry_price": 200}


def test_observed_at_the_line_approves_a_full_market_exit():
    result = evaluate(trigger(observed="160.00"), context(**HELD), CONFIG)
    order = result.verdict.order
    assert (order.side, order.qty, order.order_type) == ("sell", 50, "market")
    assert (order.source, order.exposure) == ("stop_loss", "decrease")
    assert order.limit_price is None


def test_one_cent_above_the_line_is_not_a_breach():
    result = evaluate(trigger(observed="160.01"), context(**HELD), CONFIG)
    assert result.verdict.rejection_rule == rules.STOP_LOSS_NOT_BREACHED


def test_far_below_the_line_is_approved():
    assert evaluate(trigger(observed=120), context(**HELD), CONFIG).verdict.approved


def test_trigger_for_a_symbol_not_held_is_rejected():
    assert evaluate(trigger(), context(), CONFIG).verdict.rejection_rule == rules.NO_POSITION


def test_no_hard_stop_blocks_a_stop_loss_exit():
    everything_wrong = context(
        increase_orders_approved_today=5,
        halt_active=True,
        trading_paused=True,
        equity=None,
        cash=None,
        baseline_equity=None,
        reference=None,
        **HELD,
    )
    result = evaluate(trigger(observed=150), everything_wrong, CONFIG)
    assert result.verdict.approved and result.verdict.order.qty == 50


def test_market_closed_still_rejects_a_trigger():
    result = evaluate(trigger(observed=150), context(market_open=False, **HELD), CONFIG)
    assert result.verdict.rejection_rule == rules.MARKET_CLOSED


def test_a_trigger_seeing_the_loss_line_records_the_halt_and_still_exits():
    # Moved here from US3's tests: triggers exist only from this story on.
    ctx = context(baseline_equity=100000, equity=80000, cash=80000, **HELD)
    result = evaluate(trigger(observed=150), ctx, CONFIG)
    assert result.verdict.approved
    assert result.record_halt is True


def test_the_gate_uses_its_own_entry_price_not_the_triggers():
    # A trigger claiming a breach at $170 is rejected: the position's own entry
    # of $200 puts the line at $160.
    result = evaluate(trigger(observed=170), context(**HELD), CONFIG)
    assert result.verdict.rejection_rule == rules.STOP_LOSS_NOT_BREACHED


def test_a_trigger_older_than_ten_minutes_is_stale():
    # ADR 0014: an old observation may no longer be true.
    from datetime import timedelta

    from tests.unit.risk.builders import NOW

    ctx = context(shares_held=50, avg_entry_price=D(200))
    old = trigger(observed=150, observed_at=NOW - timedelta(minutes=10, seconds=1))
    assert evaluate(old, ctx, config()).verdict.rejection_rule == rules.STOP_LOSS_TRIGGER_STALE
    fresh = trigger(observed=150, observed_at=NOW - timedelta(minutes=10))
    assert evaluate(fresh, ctx, config()).verdict.approved


def test_staleness_comes_after_market_closed_and_before_no_position():
    from datetime import timedelta

    from tests.unit.risk.builders import NOW

    old = trigger(observed=150, observed_at=NOW - timedelta(hours=1))
    closed = context(market_open=False, shares_held=50, avg_entry_price=D(200))
    assert evaluate(old, closed, config()).verdict.rejection_rule == rules.MARKET_CLOSED
    unheld = context(shares_held=0)
    assert evaluate(old, unheld, config()).verdict.rejection_rule == rules.STOP_LOSS_TRIGGER_STALE
