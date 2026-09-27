from datetime import UTC, date, datetime

import pytest

from trading_agent.risk import calendar


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_regular_session_minute_is_open():
    now = utc(2026, 9, 28, 14, 0)  # Monday 10:00 ET
    assert calendar.market_open(now) is True
    assert calendar.trading_day(now) == date(2026, 9, 28)


def test_one_minute_before_the_open_is_closed():
    assert calendar.market_open(utc(2026, 9, 28, 13, 29)) is False


def test_weekend_is_closed():
    assert calendar.market_open(utc(2026, 9, 26, 15, 0)) is False


def test_thanksgiving_is_closed():
    assert calendar.market_open(utc(2026, 11, 26, 15, 0)) is False


def test_early_close_day_after_thanksgiving():
    assert calendar.market_open(utc(2026, 11, 27, 17, 59)) is True  # 12:59 ET
    assert calendar.market_open(utc(2026, 11, 27, 18, 1)) is False  # 13:01 ET


def test_open_time_is_0930_new_york_in_utc():
    assert calendar.open_time(date(2026, 9, 28)) == utc(2026, 9, 28, 13, 30)


def test_open_time_rejects_a_non_session_day():
    with pytest.raises(ValueError):
        calendar.open_time(date(2026, 9, 26))


def test_trading_day_uses_the_new_york_date():
    # 01:00 UTC on the 29th is still the evening of the 28th in New York.
    assert calendar.trading_day(utc(2026, 9, 29, 1, 0)) == date(2026, 9, 28)


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        calendar.market_open(datetime(2026, 9, 28, 14, 0))


def test_close_time_is_1600_new_york_in_utc():
    assert calendar.close_time(date(2026, 9, 28)) == utc(2026, 9, 28, 20, 0)


def test_close_time_follows_an_early_close():
    assert calendar.close_time(date(2026, 11, 27)) == utc(2026, 11, 27, 18, 0)


def test_close_time_rejects_a_non_session_day():
    with pytest.raises(ValueError):
        calendar.close_time(date(2026, 9, 26))


def test_is_session():
    assert calendar.is_session(date(2026, 9, 28)) is True
    assert calendar.is_session(date(2026, 9, 26)) is False
    assert calendar.is_session(date(2026, 11, 26)) is False
