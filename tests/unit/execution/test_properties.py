"""SC-004 and determinism as properties over 10,000 generated live states.

A failure here is a real counterexample: Hypothesis prints the shrunk minimal
input. Fix the check, then add that input as an example test.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.unit.execution.builders import NOW, approved_buy, approved_sell, repo_config, session
from trading_agent.execution import reasons
from trading_agent.execution.broker import Quote
from trading_agent.execution.checks import check_buy, check_exit
from trading_agent.execution.model import BuyLive, ExitLive, Refuse, Submit

CONFIG = repo_config()
HUNDRED = Decimal(100)
PROPERTY = settings(
    max_examples=10_000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)


def dec(lo, hi, places=2):
    return st.decimals(
        min_value=lo, max_value=hi, places=places, allow_nan=False, allow_infinity=False
    )


@st.composite
def buy_scenarios(draw):
    """Buy-ready states near the limits, so most reach rows 9-11 (the 002 lesson:
    a property that never reaches its branch checks nothing)."""
    equity = draw(dec(1_000, 10_000_000))
    cash = equity * draw(dec("0.15", "1", 4))
    ask = draw(dec(5, 2_000, 4))
    ceiling = (ask * draw(dec("0.995", "1.02", 4))).quantize(Decimal("0.01"))
    held = int(equity * draw(dec(0, "0.09", 4)) / ask)
    open_qty = int(equity * draw(dec(0, "0.03", 4)) / ask)
    open_cost = cash * draw(dec(0, "0.4", 4))
    qty = max(1, int(equity * draw(dec("0.001", "0.1", 4)) / ask))
    baseline = equity * draw(dec("0.9", "1.3", 4))
    low = draw(st.one_of(st.none(), st.just(equity), dec(1, 10_000_000)))
    live = BuyLive(
        paused=False,
        baseline=baseline,
        config=CONFIG,
        equity=equity,
        cash=cash,
        min_equity_since_open=low,
        held_qty=Decimal(held),
        open_buy_qty_symbol=Decimal(open_qty),
        open_buy_cost_all=open_cost,
        ask=Quote("AAPL", ask, NOW - timedelta(seconds=draw(st.integers(0, 60)))),
    )
    return approved_buy(qty=qty, ceiling=str(ceiling)), live


_outcomes: Counter = Counter()


@PROPERTY
@given(buy_scenarios())
def test_no_submitted_buy_breaches_the_ceiling_or_the_reserve(scenario):
    approval, live = scenario
    outcome = check_buy(approval, live, session())
    _outcomes["submit" if isinstance(outcome, Submit) else getattr(outcome, "reason", "retry")] += 1
    if not isinstance(outcome, Submit):
        return
    price = outcome.request.limit_price
    qty = Decimal(outcome.request.qty)
    equity = live.equity
    assert price <= approval.order.limit_price
    assert (live.held_qty + live.open_buy_qty_symbol + qty) * price <= (
        CONFIG.max_position_pct / HUNDRED * equity
    )
    assert live.cash - live.open_buy_cost_all - qty * price >= (
        CONFIG.cash_reserve_pct / HUNDRED * equity
    )
    line = live.baseline * (1 - CONFIG.daily_loss_halt_pct / HUNDRED)
    assert equity > line
    assert live.min_equity_since_open is None or live.min_equity_since_open > line


def test_the_buy_property_reached_every_branch_that_matters():
    # Runs after the property in file order.
    assert _outcomes["submit"] >= 500, _outcomes
    for reason in (
        reasons.QUOTE_ABOVE_CEILING,
        reasons.MAX_POSITION_PCT,
        reasons.CASH_RESERVE_PCT,
        reasons.DAILY_LOSS_LINE_CROSSED,
    ):
        assert _outcomes[reason] >= 100, _outcomes


@PROPERTY
@given(buy_scenarios())
def test_the_same_buy_state_gives_the_same_outcome(scenario):
    approval, live = scenario
    assert check_buy(approval, live, session()) == check_buy(approval, live, session())


@PROPERTY
@given(
    held=dec(0, 100_000, 0),
    open_sell=dec(0, 100_000, 0),
    qty=st.integers(1, 100_000),
    source=st.sampled_from(["decision", "stop_loss"]),
)
def test_an_exit_is_refused_only_for_its_own_reasons(held, open_sell, qty, source):
    live = ExitLive(held_qty=held, open_sell_qty_symbol=open_sell)
    outcome = check_exit(approved_sell(qty=qty, source=source), live, session())
    if isinstance(outcome, Refuse):
        assert outcome.reason == reasons.SHARES_HELD_DIFFER
        assert held - open_sell < qty
    else:
        assert isinstance(outcome, Submit) and outcome.request.qty == qty
