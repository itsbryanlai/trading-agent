"""screen.assess: one case per skip reason, and the Candidate an eligible name gives
(specs/011 research O4, O5; contracts/oi-interface.md "Closed sets")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.unit.opportunistic_identifier.support import (
    FRESH,
    MAX_AGE,
    NOW,
    UNIVERSE,
    D,
    inputs,
)
from trading_agent.opportunistic_identifier import screen
from trading_agent.opportunistic_identifier.screen import Candidate, Skip


def assess(*args, **kwargs):
    return screen.assess(*args, NOW, UNIVERSE, MAX_AGE, **kwargs)


def reason(**changes) -> str | None:
    result = assess(*inputs(**changes))
    return result.reason if isinstance(result, Skip) else None


def test_an_eligible_name_gives_a_candidate_with_both_measures():
    result = assess(*inputs(current="190", previous_close="200", high_52w="250"))
    assert isinstance(result, Candidate)
    assert result.symbol == "ACME"
    assert result.move_today == Decimal("-0.05")  # (190 - 200) / 200
    assert result.below_high == Decimal("0.24")  # (250 - 190) / 250
    assert result.row.market_cap_usd == Decimal("3000000000000.00")


def test_below_high_is_negative_when_the_price_is_above_the_stored_high():
    result = assess(*inputs(current="260", previous_close="250", high_52w="250"))
    assert result.below_high == Decimal("-0.04")


# --- listing first (research O5 step 1) -------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"type": None}, "missing_type"),
        ({"mic": None}, "missing_mic"),
        ({"conflicting": True}, "conflicting_listing"),
        ({"symbol": "BRK.B"}, "share_class_unverified"),
        ({"symbol": "BF-B"}, "share_class_unverified"),
        ({"type": "ETP"}, "universe_listing"),
        ({"type": "ADR"}, "universe_listing"),
        ({"mic": "OTCM"}, "universe_listing"),
        ({"mic": "ARCX"}, "universe_listing"),
    ],
)
def test_listing_stop_needs_no_per_name_data(changes, expected):
    symbol, listing, *_ = inputs(**changes)
    stop = screen.listing_stop(symbol, listing)
    assert stop == Skip(symbol, expected)
    # And assess gives the same answer, whatever else is wrong.
    assert assess(*inputs(**changes, quote_time=None)) == stop


def test_a_symbol_not_on_the_list_is_not_listed():
    assert screen.listing_stop("GHOST", None) == Skip("GHOST", "not_listed")


@pytest.mark.parametrize("mic", ["XNYS", "XNAS", "XASE", "XNGS", "XNMS", "XNCM"])
def test_a_common_stock_on_an_allowed_exchange_passes_the_listing_stop(mic):
    symbol, listing, *_ = inputs(mic=mic)
    assert screen.listing_stop(symbol, listing) is None


# --- the quote ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "when",
    [
        None,
        NOW - timedelta(days=1),
        NOW - timedelta(minutes=16),
        NOW - MAX_AGE - timedelta(seconds=1),
        NOW - timedelta(hours=7),  # today, but long ago
    ],
)
def test_a_quote_not_fresh_enough_is_stale(when):
    assert reason(quote_time=when) == "stale_quote"


@pytest.mark.parametrize("when", [FRESH, NOW - MAX_AGE, NOW, NOW + timedelta(seconds=30)])
def test_a_quote_within_the_limit_passes(when):
    assert reason(quote_time=when) is None


def test_a_quote_from_the_last_session_is_stale_even_inside_a_long_age_limit():
    # Friday 09:35 ET, with Thursday's last trade at 15:55 ET: not today's session.
    friday_open = datetime(2026, 10, 9, 13, 35, tzinfo=UTC)
    thursday_close = datetime(2026, 10, 8, 19, 55, tzinfo=UTC)
    result = screen.assess(
        *inputs(quote_time=thursday_close), friday_open, UNIVERSE, timedelta(hours=24)
    )
    assert result == Skip("ACME", "stale_quote")


@pytest.mark.parametrize("field", ["current", "previous_close"])
@pytest.mark.parametrize("value", [None, "0", 0])
def test_a_missing_or_zero_price_is_skipped(field, value):
    assert reason(**{field: value}) == "missing_price"


# --- reference.normalize failures (research O4) ---------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"currency": "EUR"}, "non_usd_market_cap"),
        ({"currency": None}, "non_usd_market_cap"),
        ({"market_cap_millions": None}, "missing_market_cap"),
        ({"market_cap_millions": "0"}, "missing_market_cap"),
        ({"avg_volume_10d_millions": None}, "missing_volume"),
        ({"avg_volume_10d_millions": "0"}, "missing_volume"),
        ({"market_cap_millions": "30000000"}, "implausible_market_cap"),  # $30 trillion
        ({"avg_volume_10d_millions": "99999999"}, "implausible_dollar_volume"),
        ({"market_cap_millions": "1e30", "avg_volume_10d_millions": "1"}, "implausible_market_cap"),
    ],
)
def test_normalize_failures_are_skipped_under_the_jobs_own_reason(changes, expected):
    assert reason(**changes) == expected


# --- the gate's universe floors (research O4) -----------------------------------------------------

CAP_FLOOR_MILLIONS = UNIVERSE.min_market_cap_usd / 1_000_000  # 500
VOLUME_FLOOR = UNIVERSE.min_avg_daily_dollar_volume_usd  # 10,000,000
PRICE_FLOOR = UNIVERSE.min_share_price_usd  # 5


def test_the_floors_the_cases_below_are_built_on():
    assert (CAP_FLOOR_MILLIONS, VOLUME_FLOOR, PRICE_FLOOR) == (500, 10_000_000, 5)


def test_market_cap_just_under_the_floor_is_skipped_and_the_floor_itself_passes():
    base = {"avg_volume_10d_millions": "0.1"}  # $20M a day at 200: under the cap, over its floor
    assert reason(**base, market_cap_millions=str(CAP_FLOOR_MILLIONS - Decimal("0.001"))) == (
        "universe_market_cap"
    )
    assert reason(**base, market_cap_millions=str(CAP_FLOOR_MILLIONS)) is None


def test_dollar_volume_just_under_the_floor_is_skipped_and_the_floor_itself_passes():
    # At a previous close of 200, 0.05 million shares is exactly $10,000,000.
    assert reason(avg_volume_10d_millions="0.049999") == "universe_dollar_volume"
    assert reason(avg_volume_10d_millions="0.05") is None


def test_share_price_just_under_the_floor_is_skipped_and_the_floor_itself_passes():
    base = {"avg_volume_10d_millions": "3", "high_52w": "9"}
    assert reason(**base, previous_close="4.9999", current="4.9") == "universe_share_price"
    assert reason(**base, previous_close="5", current="4.9") is None


def test_the_universe_rule_uses_the_derived_row_not_the_raw_values():
    # The row's price is the previous close, so a current price over the floor doesn't help.
    assert reason(previous_close="4", current="6", avg_volume_10d_millions="3") == (
        "universe_share_price"
    )


# --- completeness and plausibility ---------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "0"])
def test_a_missing_52_week_high_is_skipped(value):
    assert reason(high_52w=value) == "missing_52_week_high"


def test_either_pe_or_pb_is_enough():
    assert reason(pe_ttm=None, pb="3") is None
    assert reason(pe_ttm="20", pb=None) is None
    assert reason(pe_ttm=None, pb=None) == "missing_fundamentals"


@pytest.mark.parametrize(
    ("current", "expected"),
    [("300.01", "implausible_move"), ("99.99", "implausible_move"), ("300", None), ("100", None)],
)
def test_a_move_beyond_fifty_percent_is_implausible_and_fifty_passes(current, expected):
    assert reason(current=current, high_52w="400") == expected


def test_name_and_industry_are_cleaned_and_cut_to_100_characters():
    result = assess(*inputs(description="A\x00" + "n" * 200, industry="I\x07" + "i" * 200))
    assert result.data.name == "A" + "n" * 99
    assert result.data.industry == "I" + "i" * 99


def test_a_none_name_or_industry_stays_none():
    result = assess(*inputs(description=None, industry=None))
    assert (result.data.name, result.data.industry) == (None, None)


def test_the_data_keeps_what_was_fetched_and_when():
    result = assess(*inputs())
    assert result.data.fetched_at == NOW
    assert result.data.quote.timestamp == FRESH
    assert result.data.fundamentals.pe_ttm == D("20")
