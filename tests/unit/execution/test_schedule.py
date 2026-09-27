"""US5, US6: when the monitor and the pre-open snapshot are due (research E13)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_agent.execution.schedule import monitor_window, pre_open_due


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_windows_start_at_the_open_every_thirty_minutes():
    assert monitor_window(utc(2026, 9, 28, 13, 30)) == (
        utc(2026, 9, 28, 13, 30),
        utc(2026, 9, 28, 14, 0),
    )
    assert monitor_window(utc(2026, 9, 28, 14, 10))[0] == utc(2026, 9, 28, 14, 0)
    assert monitor_window(utc(2026, 9, 28, 19, 59))[0] == utc(2026, 9, 28, 19, 30)


@pytest.mark.parametrize(
    "now",
    [
        utc(2026, 9, 28, 13, 29),  # before the open
        utc(2026, 9, 28, 20, 0),  # at the close
        utc(2026, 9, 26, 15, 0),  # Saturday
        utc(2026, 11, 26, 15, 0),  # Thanksgiving
        utc(2026, 11, 27, 18, 0),  # after the early close
    ],
)
def test_no_window_outside_market_hours(now):
    assert monitor_window(now) is None


def test_the_last_window_is_clipped_at_an_early_close():
    assert monitor_window(utc(2026, 11, 27, 17, 45)) == (
        utc(2026, 11, 27, 17, 30),
        utc(2026, 11, 27, 18, 0),
    )


def test_pre_open_is_due_in_the_hour_before_the_open_until_one_exists():
    assert pre_open_due(utc(2026, 9, 28, 12, 30), False)
    assert pre_open_due(utc(2026, 9, 28, 13, 29, 59), False)
    assert not pre_open_due(utc(2026, 9, 28, 12, 29), False)
    assert not pre_open_due(utc(2026, 9, 28, 13, 30), False)
    assert not pre_open_due(utc(2026, 9, 28, 13, 0), True)


def test_pre_open_is_never_due_on_a_weekend_or_holiday():
    assert not pre_open_due(utc(2026, 9, 26, 13, 0), False)
    assert not pre_open_due(utc(2026, 11, 26, 14, 0), False)


def test_pre_open_on_an_early_close_day_is_relative_to_that_days_open():
    assert pre_open_due(utc(2026, 11, 27, 14, 0), False)  # open is 14:30 UTC in EST
    assert not pre_open_due(utc(2026, 11, 27, 13, 29), False)
