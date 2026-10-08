"""Rotation: which slice of the scan list a run fetches (specs/011-opportunistic-identifier
research O3, SC-003). Pure code: `now` and `today` are arguments."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trading_agent.opportunistic_identifier import rotation as r
from trading_agent.opportunistic_identifier.config import Slots
from trading_agent.orchestrator import config as schedule_config
from trading_agent.orchestrator import planner
from trading_agent.risk import calendar

ET = ZoneInfo("America/New_York")
SLOTS = Slots(time(10, 0), time(15, 0), 60, 30)
THURSDAY = date(2026, 10, 8)
EARLY_CLOSE = date(2026, 11, 27)  # the Friday after Thanksgiving: close 13:00 ET
HOLIDAY = date(2026, 11, 26)
SATURDAY = date(2026, 10, 10)
MONDAY = date(2026, 10, 12)


def et(day: date, hh_mm: str) -> datetime:
    hour, minute = (int(part) for part in hh_mm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def hh_mm(slots: list[datetime]) -> list[str]:
    return [s.astimezone(ET).strftime("%H:%M") for s in slots]


# --- day_slots -------------------------------------------------------------------------


def test_a_normal_day_has_six_slots_from_ten_to_three():
    assert hh_mm(r.day_slots(THURSDAY, SLOTS)) == [
        "10:00",
        "11:00",
        "12:00",
        "13:00",
        "14:00",
        "15:00",
    ]


def test_an_early_close_day_has_three_slots_capped_before_the_close():
    assert calendar.close_time(EARLY_CLOSE) == et(EARLY_CLOSE, "13:00").astimezone(UTC)
    assert hh_mm(r.day_slots(EARLY_CLOSE, SLOTS)) == ["10:00", "11:00", "12:00"]


@pytest.mark.parametrize("day", [SATURDAY, HOLIDAY, date(2026, 10, 11)])
def test_a_non_session_day_has_no_slots(day):
    assert r.day_slots(day, SLOTS) == []


def test_slots_are_aware_eastern_times():
    first = r.day_slots(THURSDAY, SLOTS)[0]
    assert first.utcoffset() == timedelta(hours=-4)  # EDT
    assert r.day_slots(EARLY_CLOSE, SLOTS)[0].utcoffset() == timedelta(hours=-5)  # EST


def test_a_different_slot_rule_is_honored():
    slots = Slots(time(9, 45), time(14, 0), 45, 0)
    assert hh_mm(r.day_slots(THURSDAY, slots)) == [
        "09:45",
        "10:30",
        "11:15",
        "12:00",
        "12:45",
        "13:30",
    ]


SAMPLE_DAYS = [date(2026, 1, 2) + timedelta(days=17 * i) for i in range(20)] + [
    EARLY_CLOSE,
    HOLIDAY,
    date(2026, 12, 24),  # early close
    date(2026, 7, 3),  # Independence Day, observed
    SATURDAY,
]


def test_day_slots_is_exactly_the_orchestrators_rule():
    # The agent may not import the orchestrator; a test may (research O3).
    schedule = schedule_config.load_config()
    for day in SAMPLE_DAYS:
        expected = [when for _, when in planner.oi_slots(day, schedule)]
        assert r.day_slots(day, SLOTS) == expected, day
    assert any(len(r.day_slots(day, SLOTS)) == 3 for day in SAMPLE_DAYS)  # an early close


# --- slot_number -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("09:45", 0),
        ("10:00", 0),
        ("10:59", 0),
        ("11:00", 1),
        ("11:30", 1),
        ("15:00", 5),
        ("15:59", 5),
    ],
)
def test_slot_number_is_the_latest_slot_at_or_before_now(clock, expected):
    assert r.slot_number(et(THURSDAY, clock), SLOTS) == expected


def test_slot_number_on_an_early_close_day():
    assert r.slot_number(et(EARLY_CLOSE, "12:30"), SLOTS) == 2
    assert r.slot_number(et(EARLY_CLOSE, "13:01"), SLOTS) == 2


def test_slot_number_accepts_utc_and_on_a_non_session_day_is_zero():
    assert r.slot_number(datetime(2026, 10, 8, 15, 0, tzinfo=UTC), SLOTS) == 1  # 11:00 ET
    assert r.slot_number(et(SATURDAY, "12:00"), SLOTS) == 0


# --- run_index ------------------------------------------------------------------------------


def _sessions_before(day: date) -> list[date]:
    days, d = [], r.EPOCH
    while d < day:
        if calendar.is_session(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def test_the_epoch_is_the_first_session_of_2026_and_its_first_slot_is_index_zero():
    assert r.EPOCH == date(2026, 1, 2) and calendar.is_session(r.EPOCH)
    assert r.run_index(r.EPOCH, et(r.EPOCH, "10:00"), SLOTS) == 0


@pytest.mark.parametrize("day", [date(2026, 3, 9), THURSDAY, EARLY_CLOSE, date(2027, 1, 4)])
def test_run_index_counts_every_slot_on_every_earlier_session(day):
    schedule = schedule_config.load_config()
    before = sum(len(planner.oi_slots(d, schedule)) for d in _sessions_before(day))
    assert r.run_index(day, et(day, "10:00"), SLOTS) == before
    assert r.run_index(day, et(day, "12:20"), SLOTS) == before + 2


def test_consecutive_slots_get_consecutive_indices_across_weekends_holidays_and_early_closes():
    indices = []
    day = date(2026, 11, 23)  # the Thanksgiving week and the Monday after
    while day <= date(2026, 12, 1):
        indices += [r.run_index(day, when, SLOTS) for when in r.day_slots(day, SLOTS)]
        day += timedelta(days=1)
    assert len(indices) == 3 * 6 + 0 + 3 + 6 + 6  # Mon-Wed, the holiday, the early close, Mon, Tue
    assert indices == list(range(indices[0], indices[0] + len(indices)))


# --- slice_for ----------------------------------------------------------------------------


def test_slice_for_takes_consecutive_batches_of_the_sorted_universe():
    universe = [f"S{n}" for n in range(10)]
    now = et(THURSDAY, "11:00")
    index = r.run_index(THURSDAY, now, SLOTS)
    got = r.slice_for(universe, THURSDAY, now, SLOTS, 4)
    assert (got.run_index, got.batches, got.batch) == (index, 3, index % 3)
    start = (index % 3) * 4
    assert got.symbols == tuple(universe[start : start + 4])


def test_the_last_batch_is_short_and_a_universe_inside_one_slice_is_one_batch():
    universe = ["A", "B", "C", "D", "E"]
    seen = [
        r.slice_for(universe, THURSDAY, et(THURSDAY, f"{h}:00"), SLOTS, 2).symbols
        for h in (10, 11, 12)
    ]
    assert sorted(seen) == [("A", "B"), ("C", "D"), ("E",)]
    whole = r.slice_for(universe, THURSDAY, et(THURSDAY, "12:00"), SLOTS, 5)
    assert (whole.batches, whole.batch, whole.symbols) == (1, 0, tuple(universe))


def test_an_unsorted_universe_is_scanned_in_sorted_order():
    got = r.slice_for(["C", "A", "B"], THURSDAY, et(THURSDAY, "10:00"), SLOTS, 3)
    assert got.symbols == ("A", "B", "C")


def test_an_empty_universe_has_no_batches_and_no_symbols():
    got = r.slice_for([], THURSDAY, et(THURSDAY, "11:00"), SLOTS, 40)
    assert (got.batches, got.batch, got.symbols) == (0, 0, ())


def test_a_non_session_day_uses_the_next_session_at_slot_zero():
    universe = [f"S{n}" for n in range(30)]
    saturday = r.slice_for(universe, SATURDAY, et(SATURDAY, "11:00"), SLOTS, 4)
    monday = r.slice_for(universe, MONDAY, et(MONDAY, "10:00"), SLOTS, 4)
    assert saturday == monday
    holiday = r.slice_for(universe, HOLIDAY, et(HOLIDAY, "11:00"), SLOTS, 4)
    friday = r.slice_for(universe, EARLY_CLOSE, et(EARLY_CLOSE, "10:00"), SLOTS, 4)
    assert holiday == friday


def test_slice_for_is_slice_at_the_run_index():
    universe = [f"S{n}" for n in range(37)]
    now = et(EARLY_CLOSE, "12:00")
    index = r.run_index(EARLY_CLOSE, now, SLOTS)
    assert r.slice_for(universe, EARLY_CLOSE, now, SLOTS, 5) == r.slice_at(universe, index, 5)


# --- SC-003: even coverage -----------------------------------------------------------------

symbols = st.integers(min_value=1, max_value=1000).map(lambda n: [f"T{i:04d}" for i in range(n)])


@given(universe=symbols, slice_size=st.integers(1, 200), start=st.integers(0, 10**6))
def test_any_batches_consecutive_run_indices_cover_every_symbol_exactly_once(
    universe, slice_size, start
):
    batches = math.ceil(len(universe) / slice_size)
    covered = [
        s
        for k in range(start, start + batches)
        for s in r.slice_at(universe, k, slice_size).symbols
    ]
    assert sorted(covered) == universe  # every symbol, and none twice


@settings(max_examples=15, deadline=None)
@given(
    size=st.integers(1, 12).flatmap(
        lambda batches: st.tuples(st.just(batches), st.integers(1, 60))
    ),
    first_day=st.dates(
        date(2026, 1, 2), date(2027, 3, 31)
    ),  # exchange_calendars ends a year ahead,
)
def test_running_every_real_slot_for_batches_slots_covers_every_symbol_once(size, first_day):
    # Through the calendar: weekends, holidays and early closes included. The batch count is
    # kept small because each slice_for counts every session since the epoch.
    batches, slice_size = size
    universe = [f"T{i:04d}" for i in range(batches * slice_size - slice_size // 2)]
    assert math.ceil(len(universe) / slice_size) == batches
    slots: list[tuple[date, datetime]] = []
    day = first_day
    while len(slots) < batches:
        slots += [(day, when) for when in r.day_slots(day, SLOTS)]
        day += timedelta(days=1)
    covered = [
        s
        for d, when in slots[:batches]
        for s in r.slice_for(universe, d, when, SLOTS, slice_size).symbols
    ]
    assert sorted(covered) == universe
