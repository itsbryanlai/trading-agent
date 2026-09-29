"""normalize(): provider values in, one stored row or one failure out (research D2-D5)."""

from __future__ import annotations

import re
from decimal import Decimal
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


def run(
    *,
    type="Common Stock",
    mic="XNGS",
    cap="1415993",
    pc="150.25",
    vol="32.50147",
    listed=True,
    symbol="AAPL",
):
    def d(v):
        return None if v is None else Decimal(v)

    listing = Listing(symbol, type, mic) if listed else None
    return n.normalize(
        symbol,
        listing,
        Profile(symbol, d(cap)),
        Quote(symbol, d(pc)),
        Metrics(symbol, d(vol)),
    )


def test_a_complete_symbol_becomes_a_row_in_dollars():
    row = run()
    assert isinstance(row, n.ReferenceRow)
    assert row.symbol == "AAPL"
    assert row.security_type == "common_stock"
    assert row.exchange_mic == "XNAS"
    assert row.market_cap_usd == Decimal("1415993000000.00")
    expected_dv = (Decimal("32.50147") * 10**6 * Decimal("150.25")).quantize(Decimal("0.01"))
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


def test_rounding_is_half_even_to_the_column_scale():
    row = run(cap="0.000125", pc="10.00005", vol="0.000001")
    assert row.market_cap_usd == Decimal("125.00")
    assert row.share_price_usd == Decimal("10.0000")  # 10.00005 -> half-even down
    assert row.avg_daily_dollar_volume_usd == Decimal("10.00")


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"listed": False}, n.NOT_LISTED),
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
    ],
)
def test_failures(kwargs, reason):
    result = run(**kwargs)
    assert isinstance(result, n.Failure)
    assert result == n.Failure("AAPL", reason)


def test_exactly_twenty_trillion_is_allowed():
    assert isinstance(run(cap="20000000", vol="1", pc="100"), n.ReferenceRow)


def test_dollar_volume_equal_to_market_cap_is_allowed():
    # 1,000,000 shares/day * $100 = $100m = market cap of 100 (millions)
    row = run(cap="100", vol="1", pc="100")
    assert row.avg_daily_dollar_volume_usd == row.market_cap_usd


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
