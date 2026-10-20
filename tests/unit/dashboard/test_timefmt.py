"""Times as the owner reads them (specs/014 tasks T005; research D19)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.dashboard import timefmt

SGT = ZoneInfo("Asia/Singapore")
NEW_YORK = ZoneInfo("America/New_York")

# The tasks.md test-clock table, in UTC.
DEFAULT = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)  # Tue 07:00 +08, Mon 19:00 EDT
IN_SESSION = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
WINTER = datetime(2026, 11, 16, 23, 0, tzinfo=UTC)  # Mon 18:00 EST
WEEKEND = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)  # Sat 22:00 EDT


def test_moment_shows_both_zones_in_summer():
    assert timefmt.moment(DEFAULT, SGT) == "20 Oct 07:00 SGT (19 Oct 19:00 ET)"
    assert timefmt.moment(IN_SESSION, SGT) == "19 Oct 23:00 SGT (19 Oct 11:00 ET)"


def test_moment_uses_the_example_from_the_contract():
    ts = datetime(2026, 10, 19, 1, 12, tzinfo=UTC)
    assert timefmt.moment(ts, SGT) == "19 Oct 09:12 SGT (18 Oct 21:12 ET)"


def test_moment_in_winter_the_new_york_part_is_still_called_et():
    assert timefmt.moment(WINTER, SGT) == "17 Nov 07:00 SGT (16 Nov 18:00 ET)"
    assert WINTER.astimezone(NEW_YORK).utcoffset() == timedelta(hours=-5)


def test_moment_on_the_weekend():
    assert timefmt.moment(WEEKEND, SGT) == "25 Oct 10:00 SGT (24 Oct 22:00 ET)"


def test_moment_pads_hours_and_not_days():
    ts = datetime(2026, 10, 5, 21, 5, tzinfo=UTC)
    assert timefmt.moment(ts, SGT) == "6 Oct 05:05 SGT (5 Oct 17:05 ET)"


def test_a_zone_without_a_letter_abbreviation_is_shown_by_its_tzdata_name():
    text = timefmt.moment(DEFAULT, ZoneInfo("Europe/London"))
    assert text == "20 Oct 00:00 BST (19 Oct 19:00 ET)"


def test_moment_converts_any_aware_timestamp_not_only_utc():
    ts = datetime(2026, 10, 19, 9, 12, tzinfo=SGT)
    assert timefmt.moment(ts, SGT) == "19 Oct 09:12 SGT (18 Oct 21:12 ET)"


def test_naive_datetimes_raise():
    naive = datetime(2026, 10, 19, 9, 12)
    with pytest.raises(ValueError):
        timefmt.moment(naive, SGT)
    with pytest.raises(ValueError):
        timefmt.age(DEFAULT, naive)
    with pytest.raises(ValueError):
        timefmt.age(naive, DEFAULT)


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=0), "just now"),
        (timedelta(seconds=59), "just now"),
        (timedelta(seconds=-30), "just now"),
        (timedelta(seconds=60), "1 min old"),
        (timedelta(minutes=3, seconds=40), "3 min old"),
        (timedelta(minutes=59, seconds=59), "59 min old"),
        (timedelta(hours=1), "1 h old"),
        (timedelta(hours=5, minutes=30), "5 h old"),
        (timedelta(hours=23, minutes=59), "23 h old"),
        (timedelta(days=1), "1 d old"),
        (timedelta(days=2, hours=3), "2 d old"),
    ],
)
def test_age(delta, expected):
    assert timefmt.age(WEEKEND, WEEKEND - delta) == expected


def test_a_weekend_snapshot_is_two_days_old_on_monday():
    snapshot = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)  # Friday's close
    assert timefmt.age(datetime(2026, 10, 25, 20, 30, tzinfo=UTC), snapshot) == "2 d old"


def test_day():
    assert timefmt.day(date(2026, 10, 19)) == "Mon 19 Oct"
    assert timefmt.day(date(2026, 11, 3)) == "Tue 3 Nov"


@given(
    st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2035, 12, 31),
        timezones=st.just(UTC),
    )
)
def test_the_et_part_always_equals_the_new_york_conversion(ts):
    text = timefmt.moment(ts, SGT)
    ny = ts.astimezone(NEW_YORK)
    assert text.endswith(f"({ny.day} {ny:%b %H:%M} ET)")
    local = ts.astimezone(SGT)
    assert text.startswith(f"{local.day} {local:%b %H:%M} SGT ")
