"""SVG chart geometry and ranges (specs/014 tasks T009; research D20)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.dashboard import charts
from trading_agent.risk import calendar

T0 = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)


def _series(*values) -> list[tuple[datetime, Decimal | None]]:
    return [
        (T0 + timedelta(hours=i), None if v is None else Decimal(str(v)))
        for i, v in enumerate(values)
    ]


def _coordinates(path: str) -> list[tuple[float, float]]:
    return [tuple(float(n) for n in pair.split(",")) for pair in path.split()]


# --- polyline --------------------------------------------------------------------------------


def test_no_points_is_an_empty_chart():
    chart = charts.polyline([])
    assert chart.empty and chart.paths == () and chart.dots == ()
    assert chart.min_label is None and chart.first_at is None


def test_only_missing_values_is_an_empty_chart():
    assert charts.polyline(_series(None, None)).empty


def test_one_point_is_a_dot_not_a_line():
    chart = charts.polyline(_series(100))
    assert not chart.empty
    assert chart.paths == () and len(chart.dots) == 1
    assert chart.min_label == chart.max_label == "100.00"
    assert chart.first_at == chart.last_at == T0


def test_a_rising_series_runs_from_bottom_left_to_top_right():
    chart = charts.polyline(_series(100, 110, 120), width=600, height=200)
    (path,) = chart.paths
    (x0, y0), (x1, y1), (x2, y2) = _coordinates(path)
    assert x0 < x1 < x2 and y0 > y1 > y2  # SVG's y grows downward
    assert chart.min_label == "100.00" and chart.max_label == "120.00"
    assert chart.first_at == T0 and chart.last_at == T0 + timedelta(hours=2)


def test_x_follows_time_not_the_index():
    points = [
        (T0, Decimal(1)),
        (T0 + timedelta(hours=1), Decimal(2)),
        (T0 + timedelta(hours=9), Decimal(3)),
    ]
    (path,) = charts.polyline(points, width=600, height=200).paths
    (x0, _), (x1, _), (x2, _) = _coordinates(path)
    assert (x1 - x0) / (x2 - x0) == pytest.approx(1 / 9, abs=0.01)


def test_a_flat_series_is_a_horizontal_line_in_the_middle():
    chart = charts.polyline(_series(5, 5, 5), width=600, height=200)
    ys = {y for _, y in _coordinates(chart.paths[0])}
    assert ys == {100.0}
    assert chart.min_label == chart.max_label == "5.00"


def test_labels_use_two_decimals_and_thousands_separators():
    chart = charts.polyline(_series("100234.5", "99876.123"))
    assert chart.min_label == "99,876.12" and chart.max_label == "100,234.50"


def test_a_missing_value_breaks_the_line_into_two_paths():
    chart = charts.polyline(_series(1, 2, None, 3, 4))
    assert len(chart.paths) == 2 and chart.dots == ()
    assert len(_coordinates(chart.paths[0])) == 2 and len(_coordinates(chart.paths[1])) == 2


def test_an_isolated_value_between_gaps_is_a_dot():
    chart = charts.polyline(_series(1, None, 2, None, 3, 4))
    assert len(chart.paths) == 1 and len(chart.dots) == 2


def test_gaps_do_not_move_the_scale_of_the_points_that_remain():
    full = charts.polyline(_series(1, 2, 3))
    gapped = charts.polyline(_series(1, None, 3))
    assert _coordinates(full.paths[0])[0] == gapped.dots[0]
    assert _coordinates(full.paths[0])[2] == gapped.dots[1]


def test_points_given_out_of_order_are_drawn_in_time_order():
    shuffled = list(reversed(_series(100, 110, 120)))
    assert charts.polyline(shuffled) == charts.polyline(_series(100, 110, 120))


def test_several_points_at_one_instant_stay_inside_the_box():
    points = [(T0, Decimal(1)), (T0, Decimal(2))]
    chart = charts.polyline(points, width=600, height=200)
    assert all(0 <= x <= 600 for x, _ in _coordinates(chart.paths[0]))


def test_naive_times_raise():
    with pytest.raises(ValueError):
        charts.polyline([(datetime(2026, 10, 19), Decimal(1))])


@given(
    st.lists(st.one_of(st.none(), st.decimals(-(10**9), 10**9, places=2)), min_size=0, max_size=40),
    st.integers(100, 1000),
    st.integers(50, 500),
)
def test_every_coordinate_stays_inside_the_box(values, width, height):
    chart = charts.polyline(_series(*values), width=width, height=height)
    for path in chart.paths:
        for x, y in _coordinates(path):
            assert 0 <= x <= width and 0 <= y <= height
    for x, y in chart.dots:
        assert 0 <= x <= width and 0 <= y <= height
    assert chart.empty == (all(v is None for v in values))


# --- rebase ----------------------------------------------------------------------------------


def test_rebase_makes_the_first_value_100():
    assert charts.rebase(_series(50, 55, 40)) == _series(100, 110, 80)


def test_rebase_skips_leading_gaps_and_keeps_later_ones():
    assert charts.rebase(_series(None, 200, None, 300)) == _series(None, 100, None, 150)


def test_rebase_of_nothing_or_a_zero_base_is_all_missing():
    assert charts.rebase([]) == []
    assert charts.rebase(_series(None, None)) == _series(None, None)
    assert charts.rebase(_series(0, 5)) == _series(None, None)


# --- ranges ----------------------------------------------------------------------------------


def _start(name: str, now: datetime):
    return charts.range_start(
        name, now, is_session=calendar.is_session, open_time=calendar.open_time
    )


def test_all_has_no_lower_bound():
    assert _start("all", T0) is None


def test_one_day_in_session_starts_at_todays_open():
    now = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)  # Mon 11:00 EDT
    assert _start("1d", now) == datetime(2026, 10, 19, 13, 30, tzinfo=UTC)


def test_one_day_after_the_close_still_starts_at_todays_open():
    now = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)  # Mon 19:00 EDT
    assert _start("1d", now) == datetime(2026, 10, 19, 13, 30, tzinfo=UTC)


def test_one_day_before_the_open_starts_at_the_last_sessions_open():
    now = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)  # Tue 07:00 EDT
    assert _start("1d", now) == datetime(2026, 10, 19, 13, 30, tzinfo=UTC)


def test_one_day_on_the_weekend_starts_at_fridays_open():
    now = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)  # Sat 22:00 EDT
    assert _start("1d", now) == datetime(2026, 10, 23, 13, 30, tzinfo=UTC)
    sunday = datetime(2026, 10, 25, 15, 0, tzinfo=UTC)
    assert _start("1d", sunday) == datetime(2026, 10, 23, 13, 30, tzinfo=UTC)


def test_one_day_after_a_holiday_skips_it():
    now = datetime(2026, 11, 26, 18, 0, tzinfo=UTC)  # Thanksgiving, closed
    assert _start("1d", now) == datetime(2026, 11, 25, 14, 30, tzinfo=UTC)  # Wed, EST


def test_a_week_and_a_month_start_at_the_beginning_of_the_new_york_day():
    now = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)  # Tue 07:00 EDT
    assert _start("1w", now) == datetime(2026, 10, 13, 4, 0, tzinfo=UTC)  # Tue 00:00 EDT
    assert _start("1m", now) == datetime(2026, 9, 20, 4, 0, tzinfo=UTC)


def test_a_week_across_the_clock_change_starts_at_new_york_midnight():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # Tue after the 1 Nov change
    assert _start("1w", now) == datetime(2026, 10, 27, 4, 0, tzinfo=UTC)  # still EDT then


def test_an_unknown_range_raises():
    with pytest.raises(ValueError):
        _start("1y", T0)


def test_a_naive_now_raises():
    with pytest.raises(ValueError):
        _start("1w", datetime(2026, 10, 19, 12, 0))
