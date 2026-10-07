"""The OI's copy of the gate's universe rule agrees with the gate (specs/011 research O4).

`screen.universe_stop` is a short copy of `risk.gate._universe_stop`, because the gate is
not touched by this feature. This property pins the copy to the original: values are
generated at, just above and just below each floor, so a changed comparison (`<` for `<=`,
a swapped floor, a missing exchange) fails here. It imports the gate's private function in
this test only.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from trading_agent.opportunistic_identifier import screen
from trading_agent.reference.normalize import ReferenceRow
from trading_agent.risk import gate
from trading_agent.risk.config import RiskConfig, UniverseConfig
from trading_agent.risk.model import Reference

CENT = Decimal("0.01")
SECURITY_TYPES = ["common_stock", "etf", "adr", "other"]
# Weighted toward a listed common stock, so the floor comparisons are reached often.
COMMON_HEAVY = ["common_stock"] * 12
LISTED_HEAVY = ["XNYS", "XNAS", "XASE"] * 4
MICS = ["XNYS", "XNAS", "XASE", "XNGS", "ARCX", "OTCM", "BATS", ""]

money = st.decimals(min_value=0, max_value=10**15, places=2, allow_nan=False, allow_infinity=False)
price = st.decimals(min_value=0, max_value=10**6, places=4, allow_nan=False, allow_infinity=False)


@st.composite
def floors(draw):
    return UniverseConfig(
        listing="us_common_equity",
        min_market_cap_usd=draw(money),
        min_avg_daily_dollar_volume_usd=draw(money),
        min_share_price_usd=draw(price.filter(lambda p: p > 0)),
    )


def around(draw, floor: Decimal, arbitrary):
    """A value at, just above or just below `floor`, or anything."""
    return draw(
        st.one_of(
            st.just(floor),
            st.just(floor + CENT),
            st.just(max(floor - CENT, Decimal(0))),
            st.just(floor + Decimal("0.0001")),
            st.just(max(floor - Decimal("0.0001"), Decimal(0))),
            arbitrary,
        )
    )


@st.composite
def cases(draw):
    universe = draw(floors())
    row = ReferenceRow(
        symbol="ACME",
        security_type=draw(st.sampled_from(COMMON_HEAVY + SECURITY_TYPES)),
        exchange_mic=draw(st.sampled_from(LISTED_HEAVY + MICS)),
        market_cap_usd=around(draw, universe.min_market_cap_usd, money),
        avg_daily_dollar_volume_usd=around(draw, universe.min_avg_daily_dollar_volume_usd, money),
        share_price_usd=around(draw, universe.min_share_price_usd, price),
    )
    return row, universe


def risk_config(universe: UniverseConfig) -> RiskConfig:
    return RiskConfig(
        max_position_pct=Decimal(8),
        cash_reserve_pct=Decimal(20),
        stop_loss_pct=Decimal(20),
        max_orders_per_day=5,
        daily_loss_halt_pct=Decimal(20),
        max_buy_price_tolerance_pct=Decimal(1),
        universe=universe,
        version="test",
    )


@given(cases())
def test_universe_stop_equals_the_gates(case):
    row, universe = case
    reference = Reference(
        row.security_type,
        row.exchange_mic,
        row.market_cap_usd,
        row.avg_daily_dollar_volume_usd,
        row.share_price_usd,
    )
    assert screen.universe_stop(row, universe) == gate._universe_stop(
        reference, risk_config(universe)
    )


def test_the_property_reaches_every_outcome():
    # A guard against a vacuous generator: each stop reason and a pass are all producible.
    seen = set()

    @given(cases())
    def collect(case):
        seen.add(screen.universe_stop(*case))

    collect()
    assert seen >= {
        None,
        "universe_listing",
        "universe_market_cap",
        "universe_dollar_volume",
        "universe_share_price",
    }
