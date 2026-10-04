"""freshness.is_fresh / staleness_reason (specs/008-portfolio-manager research P4, analyze F1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.unit.portfolio_manager.support import MAX_AGE, NOW, OPEN, quote
from trading_agent.portfolio_manager.freshness import is_fresh, staleness_reason
from trading_agent.reference.provider import Quote


def test_a_quote_a_minute_old_is_fresh():
    assert is_fresh(quote(at=NOW - timedelta(minutes=1)), NOW, MAX_AGE)


def test_the_age_limit_is_inclusive():
    assert is_fresh(quote(at=NOW - MAX_AGE), NOW, MAX_AGE)
    assert not is_fresh(quote(at=NOW - MAX_AGE - timedelta(seconds=1)), NOW, MAX_AGE)


@pytest.mark.parametrize("current", [None, "0", "-1"])
def test_a_missing_zero_or_negative_price_is_stale(current):
    assert not is_fresh(quote(current=current), NOW, MAX_AGE)


def test_a_quote_without_a_timestamp_is_stale():
    assert not is_fresh(Quote("AAPL", Decimal("200"), Decimal("199"), None), NOW, MAX_AGE)


def test_yesterdays_close_is_stale_even_when_recent_enough():
    just_open = OPEN + timedelta(minutes=2)
    yesterday_close = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)
    assert not is_fresh(quote(at=yesterday_close), just_open, timedelta(hours=24))


def test_the_open_itself_counts_as_today():
    assert is_fresh(quote(at=OPEN), OPEN + timedelta(minutes=1), MAX_AGE)
    assert not is_fresh(quote(at=OPEN - timedelta(seconds=1)), OPEN + timedelta(minutes=1), MAX_AGE)


def test_the_future_is_allowed_up_to_sixty_seconds_of_skew():
    assert is_fresh(quote(at=NOW + timedelta(seconds=59)), NOW, MAX_AGE)
    assert is_fresh(quote(at=NOW + timedelta(seconds=60)), NOW, MAX_AGE)
    assert not is_fresh(quote(at=NOW + timedelta(seconds=61)), NOW, MAX_AGE)


def test_a_non_session_day_has_no_fresh_quote():
    saturday = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
    assert not is_fresh(quote(at=saturday - timedelta(minutes=1)), saturday, MAX_AGE)


def test_an_early_close_days_last_minutes_are_fresh():
    now = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)  # 13:00 ET, the early close
    at = datetime(2026, 11, 27, 17, 59, tzinfo=UTC)  # 12:59 ET
    assert is_fresh(quote(at=at), now, MAX_AGE)


def test_each_quote_is_judged_at_its_own_time():
    """analyze F1: a quote traded 80 s after the run started, fetched 90 s in, is fresh;
    judged against the run's start it would look 'from the future'."""
    run_start = NOW
    trade = run_start + timedelta(seconds=80)
    fetched = run_start + timedelta(seconds=90)
    assert is_fresh(quote(at=trade), fetched, MAX_AGE)
    assert not is_fresh(quote(at=trade), run_start, MAX_AGE)


def test_reasons():
    assert staleness_reason(None, NOW, MAX_AGE) == "quote_missing"
    assert staleness_reason(quote(current=None), NOW, MAX_AGE) == "quote_stale"
    assert staleness_reason(quote(at=NOW - timedelta(minutes=9)), NOW, MAX_AGE) == "quote_stale"
    assert staleness_reason(quote(), NOW, MAX_AGE) is None
