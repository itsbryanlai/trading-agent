"""SC-005 as a property: whatever the provider sends, a row is always storable and sane."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import event, given, settings
from hypothesis import strategies as st

from trading_agent.reference import normalize as n
from trading_agent.reference.provider import Listing, Metrics, Profile, Quote

numbers = st.one_of(
    st.none(),
    st.decimals(allow_nan=True, allow_infinity=True, places=None),
    st.decimals(min_value=Decimal("0.00001"), max_value=Decimal("100000000"), places=6),
)
types = st.one_of(
    st.none(),
    st.sampled_from(["Common Stock", "common stock", "ETP", "ADR", "REIT", ""]),
    st.text(max_size=12),
)
mics = st.one_of(st.none(), st.sampled_from(["XNGS", "XNYS", "ARCX", ""]), st.text(max_size=6))


@settings(max_examples=600)
@given(
    listed=st.booleans(),
    type_=types,
    mic=mics,
    cap=numbers,
    price=numbers,
    volume=numbers,
)
def test_rows_are_always_sane(listed, type_, mic, cap, price, volume):
    listing = Listing("X", type_, mic) if listed else None
    result = n.normalize("X", listing, Profile("X", cap), Quote("X", price), Metrics("X", volume))
    event(type(result).__name__)
    if isinstance(result, n.Failure):
        assert result.reason in n.ALL_REASONS
        return
    assert result.security_type in {"common_stock", "etf", "adr", "other"}
    if result.security_type == "common_stock":
        assert type_.strip().casefold() == "common stock"
    for value in (
        result.market_cap_usd,
        result.avg_daily_dollar_volume_usd,
        result.share_price_usd,
    ):
        assert value.is_finite()
    assert 0 < result.share_price_usd < Decimal(10) ** 10
    assert result.share_price_usd == result.share_price_usd.quantize(Decimal("0.0001"))
    assert 0 < result.market_cap_usd <= n.MAX_MARKET_CAP_USD
    assert 0 < result.avg_daily_dollar_volume_usd <= result.market_cap_usd
    assert result.market_cap_usd == result.market_cap_usd.quantize(Decimal("0.01"))


def test_the_generator_reaches_both_outcomes():
    """Guard for the property above: it must actually produce rows, not only failures."""
    seen: set[str] = set()

    @settings(max_examples=600, database=None)
    @given(cap=numbers, price=numbers, volume=numbers)
    def probe(cap, price, volume):
        result = n.normalize(
            "X",
            Listing("X", "Common Stock", "XNGS"),
            Profile("X", cap),
            Quote("X", price),
            Metrics("X", volume),
        )
        seen.add(type(result).__name__)

    probe()
    assert seen == {"ReferenceRow", "Failure"}
