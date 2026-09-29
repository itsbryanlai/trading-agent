"""SC-005 as a property: whatever the provider sends, normalize never raises, and a
row is always storable and sane. Extreme exponents are included on purpose; the
first version of this test never produced them (adversarial review)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from trading_agent.reference import normalize as n
from trading_agent.reference.provider import Listing, Metrics, Profile, Quote

NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)

extreme = st.builds(
    lambda digits, exp: Decimal(f"{digits}E{exp}"),
    st.integers(min_value=1, max_value=10**6),
    st.integers(min_value=-999999, max_value=999999),
)
numbers = st.one_of(
    st.none(),
    st.decimals(allow_nan=True, allow_infinity=True, places=None),
    st.decimals(min_value=Decimal("0.00001"), max_value=Decimal("100000000"), places=6),
    extreme,
)
types = st.one_of(
    st.none(),
    st.sampled_from(["Common Stock", "common stock", "ETP", "ADR", "REIT", ""]),
    st.text(max_size=12),
)
mics = st.one_of(st.none(), st.sampled_from(["XNGS", "XNYS", "ARCX", ""]), st.text(max_size=6))
currencies = st.one_of(st.none(), st.sampled_from(["USD", "usd", "JPY", ""]), st.text(max_size=4))
times = st.one_of(
    st.none(),
    st.sampled_from(
        [
            datetime(2026, 9, 28, 11, 0, tzinfo=UTC),
            datetime(2026, 9, 25, 20, 0, tzinfo=UTC),
            datetime(2026, 9, 24, 20, 0, tzinfo=UTC),
        ]
    ),
    st.integers(min_value=-20 * 24 * 60, max_value=24 * 60).map(
        lambda m: NOW + timedelta(minutes=m)
    ),
)


def _normalize(listed, conflicting, type_, mic, currency, cap, current, price, when, volume):
    listing = Listing("X", type_, mic, conflicting) if listed else None
    return n.normalize(
        "X",
        listing,
        Profile("X", cap, currency),
        Quote("X", current, price, when),
        Metrics("X", volume),
        NOW,
    )


@settings(max_examples=1500)
@given(
    listed=st.booleans(),
    conflicting=st.booleans(),
    type_=types,
    mic=mics,
    currency=currencies,
    cap=numbers,
    current=numbers,
    price=numbers,
    when=times,
    volume=numbers,
)
def test_never_raises_and_rows_are_always_sane(
    listed, conflicting, type_, mic, currency, cap, current, price, when, volume
):
    result = _normalize(
        listed, conflicting, type_, mic, currency, cap, current, price, when, volume
    )
    if isinstance(result, n.Failure):
        assert result.reason in n.ALL_REASONS
        return
    assert listed and not conflicting
    assert currency.strip().upper() == "USD"
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
    # The stored price is one of the quote's two prices, rounded down, never up.
    assert result.share_price_usd <= max(p for p in (current, price) if p is not None)


def test_the_generator_reaches_rows_and_the_extreme_path():
    """Guard for the property above: it must produce rows, and reach extreme values."""
    seen: set[str] = set()

    @settings(max_examples=1500, database=None)
    @given(cap=numbers, price=numbers, volume=numbers)
    def probe(cap, price, volume):
        result = _normalize(
            True, False, "Common Stock", "XNGS", "USD", cap, price, price, NOW, volume
        )
        seen.add(result.reason if isinstance(result, n.Failure) else "row")

    probe()
    assert "row" in seen
    assert n.VALUE_OUT_OF_RANGE in seen


def test_extreme_exponents_never_raise():
    for cap, price, volume in [
        ("20000000", "1E+25", "1E-30"),
        ("1E-999999", "1", "1"),
        ("1", "1E+999999", "1E-999999"),
        ("1E+999999", "1", "1"),
    ]:
        result = _normalize(
            True,
            False,
            "Common Stock",
            "XNGS",
            "USD",
            Decimal(cap),
            Decimal(price),
            Decimal(price),
            NOW,
            Decimal(volume),
        )
        assert isinstance(result, n.Failure)
