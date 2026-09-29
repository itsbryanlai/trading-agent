"""When the job may fetch (D7, FR-013, FR-015) and when the open warning is due (FR-025)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from trading_agent.reference.schedule import fetch_allowed, open_warning_due


def utc(*args):
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    "now",
    [
        utc(2026, 9, 26, 14),  # Saturday
        utc(2026, 11, 26, 14),  # Thanksgiving
        utc(2026, 9, 28, 11, 59, 59),  # 07:59:59 ET
        utc(2026, 9, 28, 20, 0),  # the close
        utc(2026, 9, 28, 22),  # evening
        utc(2026, 11, 27, 18, 0),  # the early close
        utc(2026, 9, 29, 3),  # 23:00 ET the night before a session
    ],
)
def test_no_fetching(now):
    assert fetch_allowed(now) is False


@pytest.mark.parametrize(
    "now",
    [
        utc(2026, 9, 28, 12, 0),  # 08:00:00 ET
        utc(2026, 9, 28, 12, 30),
        utc(2026, 9, 28, 14),
        utc(2026, 9, 28, 19, 59, 59),
        utc(2026, 11, 27, 17, 59),  # before the early close
        utc(2026, 12, 7, 13, 0),  # 08:00 ET in winter (EST)
    ],
)
def test_fetching(now):
    assert fetch_allowed(now) is True


def test_winter_0759_is_still_too_early():
    assert fetch_allowed(utc(2026, 12, 7, 12, 59)) is False


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        fetch_allowed(datetime(2026, 9, 28, 14))


def test_open_warning_once_per_day_from_the_open():
    day = date(2026, 9, 28)
    assert open_warning_due(utc(2026, 9, 28, 13, 29), None) is False
    assert open_warning_due(utc(2026, 9, 28, 13, 30), None) is True
    assert open_warning_due(utc(2026, 9, 28, 13, 31), day) is False
    assert open_warning_due(utc(2026, 9, 28, 20, 0), None) is False  # after the close
    assert open_warning_due(utc(2026, 9, 29, 13, 30), day) is True  # next day
    assert open_warning_due(utc(2026, 9, 26, 14), None) is False  # weekend
