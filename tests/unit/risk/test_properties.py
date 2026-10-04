"""SC-001 to SC-003 as properties over 10,000+ generated states each (research G15).

A failure here is a real counterexample: Hypothesis prints the shrunk minimal
input. Fix the gate, then add that input as an example test.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.unit.risk.builders import NOW, PASSING_REFERENCE, TODAY, config
from trading_agent.risk import rules
from trading_agent.risk.gate import evaluate
from trading_agent.risk.model import Context, DecisionRequest, Reference, StopLossRequest

CONFIG = config()
HUNDRED = Decimal(100)
PROPERTY = settings(
    max_examples=10_000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)

money = st.decimals(
    min_value=1000, max_value=10_000_000, places=2, allow_nan=False, allow_infinity=False
)
prices = st.decimals(min_value=1, max_value=5000, places=2, allow_nan=False, allow_infinity=False)
weights = st.decimals(min_value=0, max_value=100, places=3, allow_nan=False, allow_infinity=False)
references = st.one_of(
    st.none(),
    st.just(PASSING_REFERENCE),
    st.builds(
        Reference,
        security_type=st.sampled_from(["common_stock", "etf", "adr", "other"]),
        exchange_mic=st.sampled_from(["XNYS", "XNAS", "XASE", "OTCM", "BATS"]),
        market_cap_usd=st.decimals(0, 10**13, places=2, allow_nan=False, allow_infinity=False),
        avg_daily_dollar_volume_usd=st.decimals(
            0, 10**11, places=2, allow_nan=False, allow_infinity=False
        ),
        share_price_usd=st.decimals(
            Decimal("0.01"), 5000, places=2, allow_nan=False, allow_infinity=False
        ),
    ),
)


@st.composite
def contexts(draw, market_open=None) -> Context:
    equity = draw(st.one_of(st.none(), money))
    cash = None
    if equity is not None:
        cash = draw(
            st.decimals(
                min_value=0, max_value=equity, places=2, allow_nan=False, allow_infinity=False
            )
        )
    held = draw(st.integers(0, 10_000))
    return Context(
        now=NOW,
        trading_day=TODAY,
        market_open=draw(st.booleans()) if market_open is None else market_open,
        trading_paused=draw(st.booleans()),
        halt_active=draw(st.booleans()),
        shares_held=held,
        avg_entry_price=draw(prices) if held else None,
        equity=equity,
        cash=cash,
        baseline_equity=draw(st.one_of(st.none(), money)),
        increase_orders_approved_today=draw(st.integers(0, 10)),
        reference=draw(references),
    )


@st.composite
def buy_ready_contexts(draw) -> Context:
    """No hard stop active, so every buy reaches the sizing arithmetic.

    Drawing every stop at random (as `contexts` does) almost never produces a
    state where a buy survives to sizing: measured, 0 approved buys in 10,000.
    SC-001's buy half needs this generator to test anything at all.
    """
    equity = draw(money)
    held = draw(st.integers(0, 10_000))
    return Context(
        now=NOW,
        trading_day=TODAY,
        market_open=True,
        trading_paused=False,
        halt_active=False,
        shares_held=held,
        avg_entry_price=draw(prices) if held else None,
        equity=equity,
        cash=draw(
            st.decimals(
                min_value=0, max_value=equity, places=2, allow_nan=False, allow_infinity=False
            )
        ),
        baseline_equity=equity,  # equity at the baseline: never below the loss line
        increase_orders_approved_today=draw(st.integers(0, CONFIG.max_orders_per_day - 1)),
        reference=PASSING_REFERENCE,
    )


decisions = st.builds(
    DecisionRequest,
    symbol=st.just("AAPL"),
    direction=st.sampled_from(["buy", "sell"]),
    target_weight_pct=weights,
    quote=prices,
    quote_time=st.just(NOW),
)
triggers = st.builds(
    StopLossRequest,
    symbol=st.just("AAPL"),
    observed_price=prices,
    # Fresh and stale observations alike (ADR 0014's 10-minute limit).
    observed_at=st.timedeltas(min_value=timedelta(0), max_value=timedelta(minutes=20)).map(
        lambda age: NOW - age
    ),
)


# --- SC-001: no approved order ever breaches a limit -------------------------------------

_buy_outcomes: list[str] = []


def _assert_buy_within_limits(request, ctx, order) -> None:
    # Filled at any price up to its limit, the position stays under the ceiling
    # and cash stays above the reserve.
    worst = order.limit_price
    assert order.qty >= 1
    assert (ctx.shares_held + order.qty) * worst <= CONFIG.max_position_pct / HUNDRED * ctx.equity
    assert ctx.cash - order.qty * worst >= CONFIG.cash_reserve_pct / HUNDRED * ctx.equity
    assert worst <= request.quote * (1 + CONFIG.max_buy_price_tolerance_pct / HUNDRED)


@st.composite
def buy_scenarios(draw) -> tuple[DecisionRequest, Context]:
    """A buy and a buy-ready portfolio drawn together, so the holding is a realistic
    weight at the buy's own quote. Independent draws gave mostly absurd holdings,
    and 78% of examples ended at `direction_contradicts_target`."""
    ctx = draw(buy_ready_contexts())
    quote = draw(prices)
    weight_now = draw(
        st.decimals(0, Decimal("0.12"), places=4, allow_nan=False, allow_infinity=False)
    )
    held = int(weight_now * ctx.equity / quote)
    cash_share = draw(
        st.decimals(Decimal("0.05"), 1, places=3, allow_nan=False, allow_infinity=False)
    )
    target = draw(
        st.one_of(
            st.decimals(0, 20, places=3, allow_nan=False, allow_infinity=False),
            weights,
        )
    )
    ctx = Context(
        **{
            **vars(ctx),
            "shares_held": held,
            "avg_entry_price": quote if held else None,
            "cash": (ctx.equity * cash_share).quantize(Decimal("0.01")),
        }
    )
    return DecisionRequest("AAPL", "buy", target, quote, ctx.now), ctx


@PROPERTY
@given(scenario=buy_scenarios())
def test_no_approved_buy_breaches_the_ceiling_or_the_reserve(scenario):
    request, ctx = scenario
    verdict = evaluate(request, ctx, CONFIG).verdict
    if verdict.approved:
        _buy_outcomes.append("trimmed" if verdict.order.trims else "full")
        _assert_buy_within_limits(request, ctx, verdict.order)
    else:
        _buy_outcomes.append(verdict.rejection_rule)


def test_the_buy_property_actually_exercised_approvals_and_trims():
    """Guards the property above against going vacuous again (it once saw 0 buys)."""
    if not _buy_outcomes:  # property test deselected or run in isolation
        test_no_approved_buy_breaches_the_ceiling_or_the_reserve()
    assert _buy_outcomes.count("full") >= 500
    assert _buy_outcomes.count("trimmed") >= 500


@PROPERTY
@given(request=decisions, ctx=contexts())
def test_no_approved_sell_exceeds_the_holding(request, ctx):
    verdict = evaluate(request, ctx, CONFIG).verdict
    if verdict.approved and verdict.order.side == "sell":
        assert 1 <= verdict.order.qty <= ctx.shares_held


# --- SC-002: identical inputs, identical result -------------------------------------------


@PROPERTY
@given(request=st.one_of(decisions, triggers), ctx=contexts())
def test_identical_inputs_give_an_identical_result(request, ctx):
    assert evaluate(request, ctx, CONFIG) == evaluate(request, ctx, CONFIG)


# --- SC-003: hard stops block every buy and never an exit ---------------------------------


def _hard_stop(ctx: Context) -> bool:
    ref = ctx.reference
    universe_fails = (
        ref is None
        or ref.security_type != "common_stock"
        or ref.exchange_mic not in rules.US_LISTED_MICS
        or ref.market_cap_usd < CONFIG.universe.min_market_cap_usd
        or ref.avg_daily_dollar_volume_usd < CONFIG.universe.min_avg_daily_dollar_volume_usd
        or ref.share_price_usd < CONFIG.universe.min_share_price_usd
    )
    return (
        ctx.trading_paused
        or ctx.equity is None
        or ctx.baseline_equity is None
        or ctx.halt_active
        or _crossed(ctx)
        or ctx.increase_orders_approved_today >= CONFIG.max_orders_per_day
        or universe_fails
    )


def _crossed(ctx: Context) -> bool:
    return (
        not ctx.halt_active
        and ctx.equity is not None
        and ctx.baseline_equity is not None
        and ctx.equity <= ctx.baseline_equity * (1 - CONFIG.daily_loss_halt_pct / HUNDRED)
    )


@PROPERTY
@given(request=decisions, ctx=contexts(market_open=True))
def test_every_buy_under_a_hard_stop_is_rejected(request, ctx):
    if request.direction != "buy" or not _hard_stop(ctx):
        return
    assert not evaluate(request, ctx, CONFIG).verdict.approved


@PROPERTY
@given(ctx=contexts(market_open=True), observed=prices)
def test_a_genuine_stop_loss_breach_is_always_approved(ctx, observed):
    if not ctx.shares_held:
        return
    line = ctx.avg_entry_price * (1 - CONFIG.stop_loss_pct / HUNDRED)
    verdict = evaluate(StopLossRequest("AAPL", observed, NOW), ctx, CONFIG).verdict
    if observed <= line:
        assert verdict.approved and verdict.order.qty == ctx.shares_held
    else:
        assert verdict.rejection_rule == rules.STOP_LOSS_NOT_BREACHED


@PROPERTY
@given(ctx=contexts(market_open=True), quote=prices)
def test_a_full_sell_of_a_held_position_is_always_approved(ctx, quote):
    if not ctx.shares_held:
        return
    verdict = evaluate(
        DecisionRequest("AAPL", "sell", Decimal(0), quote, ctx.now), ctx, CONFIG
    ).verdict
    assert verdict.approved and verdict.order.qty == ctx.shares_held


@PROPERTY
@given(request=st.one_of(decisions, triggers), ctx=contexts(market_open=True))
def test_the_halt_is_recorded_exactly_when_the_line_is_crossed(request, ctx):
    assert evaluate(request, ctx, CONFIG).record_halt == _crossed(ctx)
