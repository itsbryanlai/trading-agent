"""Journal view models (specs/014 tasks T024; US5 scenarios 1-5)."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.unit.dashboard import attribution_fixtures as fx
from tests.unit.dashboard.attribution_fixtures import journal_row
from trading_agent.dashboard import pages_journal as pj


def month(days: int = 5, returns="0.010000", closes=None, **agents) -> list[dict]:
    """`days` consecutive weekday rows from 5 Oct 2026, one analyst by default."""
    rows, day = [], date(2026, 10, 5)
    while len(rows) < days:
        if day.weekday() < 5:
            iso = day.isoformat()
            books = agents or {"research": fx.book("2026-10-05", "100")}
            close = "100500" if closes is None else closes[len(rows)]
            row = journal_row(iso, fx.day(iso, returns, agents=copy.deepcopy(books)))
            row["equity_close"] = None if close is None else Decimal(close)
            rows.append(row)
        day += timedelta(days=1)
    return rows


# --- query and range ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("range_raw", "page_raw", "expected"),
    [
        (None, None, ("1m", 1)),
        ("1w", "2", ("1w", 2)),
        ("1d", None, ("1w", 1)),  # a daily journal has no use for `1d`
        ("all", " 3 ", ("all", 3)),
        ("bogus", "-1", ("1m", 1)),
        ("", "x", ("1m", 1)),
    ],
)
def test_query_validation(range_raw, page_raw, expected):
    assert pj.parse_query(range_raw, page_raw) == expected


def test_since_day_is_the_new_york_day_the_range_starts():
    now = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)  # Tue 07:00 EDT
    assert pj.since_day("1w", now) == date(2026, 10, 13)
    assert pj.since_day("1m", now) == date(2026, 9, 20)
    assert pj.since_day("all", now) is None


# --- the chart (scenarios 4 and 5) ----------------------------------------------------------


def coordinates(path: str):
    return [tuple(float(n) for n in pair.split(",")) for pair in path.split()]


def test_a_line_per_analyst_and_one_for_the_account_each_starting_at_100():
    rows = month(
        20,
        research=fx.book("2026-10-05", "100"),
        opportunistic_identifier=fx.book("2026-10-05", "100"),
    )
    for i, row in enumerate(rows):  # different indices: both rebase to 100 on day one
        row["per_agent_attribution"]["agents"]["research"]["index"] = f"{100 + i}.0"
        row["per_agent_attribution"]["agents"]["opportunistic_identifier"]["index"] = (
            f"{200 + 2 * i}.0"
        )
    chart = pj.chart_view(rows)
    assert [s.name for s in chart.series] == ["account", "opportunistic_identifier", "research"]
    assert chart.series[0].css == "series-account" and chart.series[1].css == "series-1"
    first_y = {s.name: coordinates(s.paths[0])[0][1] for s in chart.series}
    assert len(set(first_y.values())) == 1  # all start at the same height: 100
    assert chart.empty_text is None and chart.first == "Mon 5 Oct" and chart.last == "Fri 30 Oct"


def test_series_share_one_value_axis_so_they_are_comparable():
    rows = month(5)
    for i, row in enumerate(rows):
        row["per_agent_attribution"]["agents"]["research"]["index"] = f"{100 + 10 * i}.0"
    chart = pj.chart_view(rows)
    by_name = {s.name: s for s in chart.series}
    research_end = coordinates(by_name["research"].paths[0])[-1][1]
    account_end = coordinates(by_name["account"].paths[0])[-1][1]
    assert research_end == 4.0  # the highest value on the chart sits at the top
    assert account_end > 150  # the account only gained ~4%
    assert chart.max_label == "140.00" and chart.min_label == "100.00"
    assert by_name["research"].last == "140.00"


def test_an_analyst_that_starts_mid_range_starts_at_100_on_its_own_day():
    rows = month(5)
    for row in rows[2:]:
        row["per_agent_attribution"]["agents"]["opportunistic_identifier"] = fx.book(
            "2026-10-07", "100"
        )
    for i, row in enumerate(rows[2:]):
        row["per_agent_attribution"]["agents"]["opportunistic_identifier"]["index"] = f"{100 + i}.0"
    chart = pj.chart_view(rows)
    by_name = {s.name: s for s in chart.series}
    late = coordinates(by_name["opportunistic_identifier"].paths[0])
    early = coordinates(by_name["research"].paths[0])
    assert len(late) == 3 and len(early) == 5
    assert late[0][0] == early[2][0]  # on the third day's x
    assert late[0][1] == early[0][1]  # at the same height as the others' first point: 100


def test_missed_sessions_are_markers_on_the_time_axis():
    rows = month(5)
    rows[3]["per_agent_attribution"]["missed_sessions"] = ["2026-10-07"]
    rows[3]["per_agent_attribution"]["sessions_covered"] = 2
    chart = pj.chart_view(rows)
    assert len(chart.markers) == 1
    x_line = coordinates(chart.series[0].paths[0])
    assert x_line[2][0] == chart.markers[0]  # the 7 Oct session is the third day


def test_missed_sessions_outside_the_rows_are_not_drawn():
    rows = month(3)
    rows[0]["per_agent_attribution"]["missed_sessions"] = ["2026-09-30"]
    assert pj.chart_view(rows).markers == ()


def series_of(chart, name):
    return next(s for s in chart.series if s.name == name)


def points_in(series) -> int:
    return sum(len(coordinates(p)) for p in series.paths) + len(series.dots)


def test_the_account_line_comes_from_equity_close_rebased_to_the_first_close():
    chart = pj.chart_view(month(4, closes=["200000", "210000", "190000", "220000"]))
    account = series_of(chart, "account")
    assert account.last == "110.00" and points_in(account) == 4
    (path,) = account.paths
    ys = [y for _, y in coordinates(path)]
    assert ys[0] > ys[1] and ys[2] > ys[0] and ys[3] < ys[1]  # 100, 105, 95, 110
    assert (chart.min_label, chart.max_label) == ("95.00", "110.00")


def test_after_a_gap_the_account_line_resumes_at_its_true_level_never_at_100():
    # 1000 -> 1100 -> (null) -> 1320: the third point is 132, not a fresh 100.
    chart = pj.chart_view(month(4, closes=["1000", "1100", None, "1320"]))
    account = series_of(chart, "account")
    assert account.last == "132.00"
    assert len(account.paths) == 1 and len(account.dots) == 1  # 100, 110 | gap | 132
    low, high = float(chart.min_label.replace(",", "")), float(chart.max_label.replace(",", ""))
    assert (low, high) == (100.0, 132.0)
    (path,) = account.paths
    assert coordinates(path)[0][1] == 196.0 and coordinates(path)[1][1] > 100
    assert account.dots[0][1] == 4.0  # the resumed point sits at the top, at 132


def test_a_row_missing_from_the_range_does_not_break_the_line_or_reset_it():
    full = month(5, closes=["1000", "1010", "1020", "1030", "1040"])
    absent = [r for i, r in enumerate(full) if i != 2]  # no row for the middle day
    account = series_of(pj.chart_view(absent), "account")
    assert len(account.paths) == 1 and points_in(account) == 4
    assert account.last == "104.00"


def test_a_null_equity_close_is_a_gap_and_the_base_is_the_first_non_null_close():
    chart = pj.chart_view(month(4, closes=[None, "2000", "2200", "1800"]))
    account = series_of(chart, "account")
    assert points_in(account) == 3 and account.last == "90.00"
    assert chart.min_label == "90.00" and chart.max_label == "110.00"


def test_all_null_closes_leave_no_account_line():
    chart = pj.chart_view(month(3, closes=[None, None, None]))
    assert "account" not in [s.name for s in chart.series if s.paths or s.dots]


def test_an_unreadable_attribution_row_gaps_the_analyst_lines_but_not_the_account():
    rows = month(5, closes=["1000", "1010", "1020", "1030", "1040"])
    rows[2]["per_agent_attribution"] = {"schema_version": 7}
    chart = pj.chart_view(rows)
    account, research = series_of(chart, "account"), series_of(chart, "research")
    assert len(account.paths) == 1 and not account.dots and points_in(account) == 5
    assert points_in(research) == 4
    assert len(research.paths) + len(research.dots) == 2  # two pieces around the gap


def test_a_single_day_is_a_dot_and_no_rows_is_an_empty_chart():
    chart = pj.chart_view(month(1))
    assert all(s.dots and not s.paths for s in chart.series)
    empty = pj.chart_view([])
    assert empty.series == () and empty.empty_text == "No journal rows for this range."
    only_bad = pj.chart_view([journal_row("2026-10-05", {"schema_version": 2})])
    # No attribution to draw, but the account's close still gives it one point.
    assert [s.name for s in only_bad.series] == ["account"] and only_bad.empty_text is None


# --- usage totals ---------------------------------------------------------------------------


def test_usage_totals_equal_the_sums_of_the_rows():
    rows = month(4, research=fx.book("2026-10-05", "100", written=2, filled=1))
    view = pj.usage_view(rows)
    assert view.labels[0] == "Written" and len(view.labels) == 7
    (row,) = view.rows
    assert row.agent == "research"
    assert row.counts == (8, 8, 4, 4, 4, 4, 4)  # defaults 2,2,1,1,1,1,0 -> written 2, filled 1
    assert view.note is None


def test_a_row_that_cant_be_read_makes_the_totals_say_they_are_partial():
    rows = month(3)
    rows[1]["per_agent_attribution"] = {"schema_version": 7}
    rows[2]["per_agent_attribution"]["agents"]["research"]["usage"]["filled"] = "0"
    view = pj.usage_view(rows)
    assert (
        view.note == "Totals are partial: 2 entries are left out (unreadable attribution or usage)."
    )
    assert pj.usage_view(month(2)).note is None


# --- day rows (scenarios 1-3) ---------------------------------------------------------------


def test_a_day_shows_equity_return_summary_and_each_agents_book():
    obj = fx.example(trading_day="2026-10-09")
    obj["account"]["return"] = "0.012345"
    view = pj.day_view(
        journal_row("2026-10-09", obj, summary="**Up** a bit.", open_="100000", close="101234.5")
    )
    assert (view.day, view.equity_open, view.equity_close) == (
        "Fri 9 Oct",
        "100,000.00",
        "101,234.50",
    )
    assert view.account_return == "+1.23%" and view.readable
    assert "<strong>Up</strong>" in view.summary.html
    (agent,) = view.agents
    assert (agent.name, agent.index, agent.day_return) == ("research", "100.00", "+0.00%")
    assert [(h.symbol, h.weight, h.ref_price) for h in agent.holdings] == [
        ("AAPL", "5.00%", "229.15")
    ]


def test_missed_sessions_unpriced_skipped_and_exits_are_named_on_the_day():
    obj = fx.example(missed_sessions=["2026-10-07", "2026-10-08"])
    book = obj["agents"]["research"]
    book.update(unpriced=["MU"], skipped_targets=["NVDA"], scaled_by="0.800000", late_reports=2)
    book["exited"] = {"sell": ["TSLA"], "holding_limit": ["AMD"]}
    view = pj.day_view(journal_row("2026-10-09", obj))
    assert view.missed == ("Wed 7 Oct", "Thu 8 Oct")
    (agent,) = view.agents
    assert agent.unpriced == ("MU",) and agent.skipped_targets == ("NVDA",)
    assert agent.exited == ("sold: TSLA", "holding limit: AMD")
    assert agent.scaled_by == "0.800000" and agent.late_reports == 2


def test_an_unknown_version_keeps_the_summary_and_says_attribution_cant_be_shown():
    view = pj.day_view(
        journal_row("2026-10-09", fx.example(schema_version=2), summary="Still here.")
    )
    assert not view.readable and view.agents == () and view.missed == ()
    assert view.unreadable_text == "Attribution can't be displayed for this day."
    assert view.equity_close == "100,500.00" and "Still here." in view.summary.html
    assert view.account_return is None


def test_a_null_account_return_shows_no_return():
    obj = fx.example()
    obj["account"]["return"] = None
    assert pj.day_view(journal_row("2026-10-09", obj)).account_return is None


def test_a_long_summary_is_collapsed_with_a_plain_summary():
    view = pj.day_view(journal_row("2026-10-09", fx.example(), summary="word " * 200))
    assert view.summary.summary and len(view.summary.summary) <= 301


def test_hostile_summary_and_symbols():
    obj = fx.example()
    obj["agents"]["research"]["holdings"]['<img src=x onerror=1>'] = {
        "weight_pct": "1", "ref_price": "1", "support_session": None,
    }  # fmt: skip
    view = pj.day_view(journal_row("2026-10-09", obj, summary="<script>alert(1)</script>"))
    assert "<script" not in view.summary.html
    assert any(h.symbol == "<img src=x onerror=1>" for h in view.agents[0].holdings)


# --- paging ---------------------------------------------------------------------------------


def test_paging_links_and_the_empty_state():
    rows = month(3)
    view = pj.journal_view(rows, rows, "1m", 1, 2)
    assert len(view.days) == 2 and view.next_url == "/journal?page=2" and view.prev_url is None
    second = pj.journal_view(rows[2:], rows, "1w", 2, 2)
    assert second.prev_url == "/journal?range=1w" and second.next_url is None
    empty = pj.journal_view([], [], "all", 3, 2)
    assert empty.empty_text and empty.prev_url == "/journal?range=all&page=2"
