"""normalize(): provider values in, one stored row or one failure out (research D2-D5)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import pytest

from trading_agent.reference import normalize as n
from trading_agent.reference.provider import Listing, Metrics, Profile, Quote

CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "004-reference-data"
    / "contracts"
    / "reference-data-interface.md"
)


NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)  # Monday 08:30 ET
ROLLED = datetime(2026, 9, 28, 11, 0, tzinfo=UTC)  # quote time today, pre-market
FRIDAY_CLOSE = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)


def run(
    *,
    type="Common Stock",
    mic="XNGS",
    cap="1415993",
    currency="USD",
    c="151.10",
    pc="150.25",
    t=ROLLED,
    vol="32.50147",
    listed=True,
    conflicting=False,
    symbol="AAPL",
    now=NOW,
):
    def d(v):
        return None if v is None else Decimal(v)

    listing = Listing(symbol, type, mic, conflicting) if listed else None
    return n.normalize(
        symbol,
        listing,
        Profile(symbol, d(cap), currency),
        Quote(symbol, d(c), d(pc), t),
        Metrics(symbol, d(vol)),
        now,
    )


def test_a_complete_symbol_becomes_a_row_in_dollars():
    row = run()
    assert isinstance(row, n.ReferenceRow)
    assert row.symbol == "AAPL"
    assert row.security_type == "common_stock"
    assert row.exchange_mic == "XNAS"
    assert row.market_cap_usd == Decimal("1415993000000.00")
    expected_dv = (Decimal("32.50147") * 10**6 * Decimal("150.25")).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    assert row.avg_daily_dollar_volume_usd == expected_dv
    assert row.share_price_usd == Decimal("150.2500")


@pytest.mark.parametrize(
    ("provider_type", "expected"),
    [
        ("Common Stock", "common_stock"),
        ("common stock", "common_stock"),
        (" COMMON STOCK ", "common_stock"),
        ("ETP", "etf"),
        ("ETF", "etf"),
        ("ADR", "adr"),
        ("REIT", "other"),
        ("Preferred", "other"),
        ("Unit", "other"),
        ("Right", "other"),
        ("Warrant", "other"),
        ("Some New Type", "other"),
        ("Common Stocks", "other"),
    ],
)
def test_type_mapping_fails_closed(provider_type, expected):
    assert run(type=provider_type).security_type == expected


@pytest.mark.parametrize(
    ("mic", "expected"),
    [
        ("XNGS", "XNAS"),
        ("XNMS", "XNAS"),
        ("XNCM", "XNAS"),
        ("XNAS", "XNAS"),
        ("XNYS", "XNYS"),
        ("XASE", "XASE"),
        ("ARCX", "ARCX"),
        ("BATS", "BATS"),
        ("OTCM", "OTCM"),
        ("OOTC", "OOTC"),
    ],
)
def test_exchange_mapping(mic, expected):
    assert run(mic=mic).exchange_mic == expected


def test_rounding_is_down_so_it_never_lifts_a_value_over_a_floor():
    row = run(cap="0.000125", pc="10.00009", vol="0.000001")
    assert row.market_cap_usd == Decimal("125.00")
    assert row.share_price_usd == Decimal("10.0000")
    assert row.avg_daily_dollar_volume_usd == Decimal("10.00")
    # $4.99995 must not become $5.0000 (the gate's price floor).
    assert run(pc="4.99995", vol="0.001").share_price_usd == Decimal("4.9999")
    # A cap just under $500m must not round up to it.
    assert run(cap="499.999999995", vol="0.001", pc="10").market_cap_usd == Decimal("499999999.99")


@pytest.mark.parametrize(
    ("t", "expected"),
    [
        (ROLLED, Decimal("150.25")),  # rolled over to today, pre-market: pc
        (datetime(2026, 9, 28, 14, 0, tzinfo=UTC), Decimal("150.25")),  # in session: pc
        (FRIDAY_CLOSE, Decimal("151.10")),  # not rolled yet: c is Friday's close
        (datetime(2026, 9, 25, 23, 0, tzinfo=UTC), Decimal("151.10")),  # Friday after hours
    ],
)
def test_previous_close_comes_from_the_quotes_session(t, expected):
    assert run(t=t).share_price_usd == expected


@pytest.mark.parametrize(
    "t",
    [
        None,
        datetime(2026, 9, 24, 20, 0, tzinfo=UTC),  # Thursday: two sessions old
        datetime(2026, 9, 1, 20, 0, tzinfo=UTC),  # a halted symbol's weeks-old quote
    ],
)
def test_stale_quotes_fail_closed(t):
    assert run(t=t) == n.Failure("AAPL", n.STALE_QUOTE)


def test_after_a_holiday_the_previous_session_is_before_the_holiday():
    friday = datetime(2026, 11, 27, 13, 0, tzinfo=UTC)
    wednesday_close = datetime(2026, 11, 25, 21, 0, tzinfo=UTC)
    assert run(now=friday, t=wednesday_close).share_price_usd == Decimal("151.10")


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"listed": False}, n.NOT_LISTED),
        ({"conflicting": True}, n.CONFLICTING_LISTING),
        ({"symbol": "BRK.B"}, n.SHARE_CLASS_UNVERIFIED),
        ({"symbol": "BF-B"}, n.SHARE_CLASS_UNVERIFIED),
        ({"currency": "JPY"}, n.NON_USD_MARKET_CAP),
        ({"currency": None}, n.NON_USD_MARKET_CAP),
        ({"currency": ""}, n.NON_USD_MARKET_CAP),
        ({"type": None}, n.MISSING_TYPE),
        ({"type": "  "}, n.MISSING_TYPE),
        ({"mic": None}, n.MISSING_MIC),
        ({"mic": ""}, n.MISSING_MIC),
        ({"cap": None}, n.MISSING_MARKET_CAP),
        ({"cap": "0"}, n.MISSING_MARKET_CAP),
        ({"cap": "-5"}, n.MISSING_MARKET_CAP),
        ({"pc": None}, n.MISSING_PRICE),
        ({"pc": "0"}, n.MISSING_PRICE),
        ({"pc": "-1"}, n.MISSING_PRICE),
        ({"vol": None}, n.MISSING_VOLUME),
        ({"vol": "0"}, n.MISSING_VOLUME),
        ({"vol": "-1"}, n.MISSING_VOLUME),
        ({"cap": "20000001"}, n.IMPLAUSIBLE_MARKET_CAP),  # > $20 trillion
        ({"cap": "1415993000000"}, n.IMPLAUSIBLE_MARKET_CAP),  # whole dollars, not millions
        ({"cap": "1000", "vol": "10", "pc": "150"}, n.IMPLAUSIBLE_DOLLAR_VOLUME),
        ({"pc": "0.00001"}, n.VALUE_OUT_OF_RANGE),  # rounds to 0.0000
        ({"pc": "10000000000", "cap": "20000000", "vol": "0.000001"}, n.VALUE_OUT_OF_RANGE),
        ({"cap": "0.0000001", "vol": "0.0000000001", "pc": "1"}, n.VALUE_OUT_OF_RANGE),
        # Extreme exponents: quantize would raise InvalidOperation (review M3).
        ({"pc": "1E+25", "vol": "1E-30", "cap": "20000000"}, n.VALUE_OUT_OF_RANGE),
    ],
)
def test_failures(kwargs, reason):
    result = run(**kwargs)
    assert isinstance(result, n.Failure)
    assert result == n.Failure(kwargs.get("symbol", "AAPL"), reason)


def test_exactly_twenty_trillion_is_allowed():
    assert isinstance(run(cap="20000000", vol="1", pc="100"), n.ReferenceRow)


def test_dollar_volume_equal_to_market_cap_is_allowed():
    # 1,000,000 shares/day * $100 = $100m = market cap of 100 (millions)
    row = run(cap="100", vol="1", pc="100")
    assert row.avg_daily_dollar_volume_usd == row.market_cap_usd


def test_currency_is_case_insensitive():
    assert isinstance(run(currency=" usd "), n.ReferenceRow)


def test_listing_failure_needs_no_provider_values():
    assert n.listing_failure("X", None) == n.Failure("X", n.NOT_LISTED)
    assert n.listing_failure("X", Listing("X", "Common Stock", "XNGS")) is None


def test_non_finite_values_fail():
    assert run(cap="Infinity").reason == n.MISSING_MARKET_CAP
    assert run(pc="NaN").reason == n.MISSING_PRICE


def test_failure_reasons_match_the_contract_table():
    text = CONTRACT.read_text()
    section = text.split("## Failure reasons", 1)[1].split("\n## ", 1)[0]
    documented = set()
    for cell in re.findall(r"^\| ([^|]+) \|", section, re.M):
        documented.update(re.findall(r"`([a-z_]+)`", cell))
    assert documented == set(n.ALL_REASONS)


def test_share_class_tickers_fail_before_any_value_is_needed():
    listing = Listing("BRK.B", "Common Stock", "XNYS")
    assert n.listing_failure("BRK.B", listing) == n.Failure("BRK.B", n.SHARE_CLASS_UNVERIFIED)
    assert n.listing_failure("GOOGL", Listing("GOOGL", "Common Stock", "XNGS")) is None
