"""Activity merge, paging and view models (specs/014 tasks T022; FR-009)."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.dashboard import activity_rows as rows
from trading_agent.dashboard import pages_activity as pa

SGT = ZoneInfo("Asia/Singapore")


def filters(**kw) -> pa.Filters:
    return pa.Filters(**kw)


# --- filters and the plan -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "problems", "expected_agent", "expected_problems"),
    [
        ("research", None, "research", False),
        ("opportunistic_identifier", "1", "opportunistic_identifier", True),
        ("portfolio_manager", "0", "portfolio_manager", False),
        ("runs", "1", "runs", True),
        ("nonsense", "yes", None, False),
        ("", "", None, False),
        (None, None, None, False),
    ],
)
def test_agent_and_problems_fall_back_to_the_default(
    agent, problems, expected_agent, expected_problems
):
    parsed = pa.parse_filters(agent, problems, None, None, None, None)
    assert (parsed.agent, parsed.problems) == (expected_agent, expected_problems)


def test_symbol_dates_and_page_use_the_shared_validation():
    parsed = pa.parse_filters(None, None, "aapl", "2026-10-01", "bad", "3")
    assert (parsed.symbol, parsed.day_from, parsed.day_to, parsed.page) == (
        "AAPL",
        date(2026, 10, 1),
        None,
        3,
    )
    since, until = pa.bounds(filters(day_from=date(2026, 10, 19), day_to=date(2026, 10, 19)), SGT)
    assert since == datetime(2026, 10, 18, 16, 0, tzinfo=UTC)
    assert until == datetime(2026, 10, 19, 16, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, pa.Plan(True, True, True, None, None)),
        ({"agent": "research"}, pa.Plan(True, False, True, "research", "research")),
        (
            {"agent": "opportunistic_identifier"},
            pa.Plan(True, False, True, "opportunistic_identifier", "opportunistic_identifier"),
        ),
        (
            {"agent": "portfolio_manager"},
            pa.Plan(False, True, True, None, "portfolio_manager"),
        ),
        ({"agent": "runs"}, pa.Plan(False, False, True, None, None)),
        ({"problems": True}, pa.Plan(False, False, True, None, None)),
        (
            {"problems": True, "agent": "research"},
            pa.Plan(False, False, True, None, "research"),
        ),
        ({"symbol": "AAPL"}, pa.Plan(True, True, False, None, None)),
        (
            {"symbol": "AAPL", "agent": "research"},
            pa.Plan(True, False, False, "research", "research"),
        ),
        ({"symbol": "AAPL", "problems": True}, pa.Plan(False, False, False, None, None)),
    ],
)
def test_plan(kw, expected):
    assert pa.plan(filters(**kw)) == expected


# --- merge order ----------------------------------------------------------------------------


def at(minutes: int) -> datetime:
    return rows.AT + timedelta(minutes=minutes)


def test_items_are_newest_first_across_the_three_kinds():
    page = pa.merge(
        [rows.report("r-1", at(10)), rows.report("r-2", at(40))],
        [rows.decision("d-1", at(30))],
        [rows.run("u-1", at(20)), rows.skipped("u-2", at(50))],
        0,
        10,
    )
    assert [(i.kind, i.id) for i in page.items] == [
        ("run", "u-2"),
        ("report", "r-2"),
        ("decision", "d-1"),
        ("run", "u-1"),
        ("report", "r-1"),
    ]
    assert page.has_next is False


def test_a_run_is_ordered_by_its_start_or_else_its_slot_time():
    started = rows.run("u-1", at(5), slot_at=at(100), started_at=at(5))
    skipped = rows.skipped("u-2", at(30))
    page = pa.merge([], [], [started, skipped], 0, 10)
    assert [i.id for i in page.items] == ["u-2", "u-1"]


def test_ties_break_by_kind_then_id_the_same_way_every_time():
    same = rows.AT
    parts = (
        [rows.report("r-b", same), rows.report("r-a", same)],
        [rows.decision("d-1", same)],
        [rows.run("u-1", same)],
    )
    first = [i.id for i in pa.merge(*parts, 0, 10).items]
    assert first == ["r-b", "r-a", "d-1", "u-1"]
    assert [i.id for i in pa.merge(*reversed_lists(parts), 0, 10).items] == first


def reversed_lists(parts):
    return tuple(list(reversed(p)) for p in parts)


def test_a_run_with_no_time_sorts_last():
    odd = rows.run("u-0", started_at=None, slot_at=None)
    page = pa.merge([rows.report("r-1", at(1))], [], [odd], 0, 10)
    assert [i.id for i in page.items] == ["r-1", "u-0"]


def test_paging_slices_and_flags_the_next_page():
    reports = [rows.report(f"r-{i:02d}", at(i)) for i in range(7)]
    first = pa.merge(reports, [], [], 0, 3)
    second = pa.merge(reports, [], [], 3, 3)
    third = pa.merge(reports, [], [], 6, 3)
    assert [len(p.items) for p in (first, second, third)] == [3, 3, 1]
    assert [p.has_next for p in (first, second, third)] == [True, True, False]
    assert pa.merge(reports, [], [], 9, 3).items == ()


_times = st.integers(0, 12)
_ids = st.integers(0, 999)


def _lists(make):
    return (
        st.lists(st.tuples(_times, _ids), max_size=40)
        .map(
            lambda pairs: sorted(
                {(t, i) for t, i in pairs}, key=lambda p: (p[0], p[1]), reverse=True
            )
        )
        .map(lambda pairs: [make(f"{i:04d}", at(t)) for t, i in pairs])
    )


@settings(max_examples=300, deadline=None)
@given(
    _lists(lambda i, t: rows.report("r" + i, t)),
    _lists(lambda i, t: rows.decision("d" + i, t)),
    _lists(lambda i, t: rows.run("u" + i, t)),
    st.integers(1, 8),
)
def test_concatenating_the_pages_equals_the_full_sorted_list(reports, decisions, runs, size):
    full = pa.merge(reports, decisions, runs, 0, 10_000)
    assert not full.has_next
    collected, offset = [], 0
    while True:
        # Each query returns only offset + size + 1 rows, as the route asks for.
        limit = offset + size + 1
        page = pa.merge(reports[:limit], decisions[:limit], runs[:limit], offset, size)
        collected.extend(page.items)
        if not page.has_next:
            break
        offset += size
    assert [(i.kind, i.id) for i in collected] == [(i.kind, i.id) for i in full.items]
    keys = [pa._sort_key(i) for i in full.items]
    assert keys == sorted(keys, reverse=True)


def test_the_property_holds_for_a_shuffled_deterministic_case():
    rng = random.Random(7)
    reports = sorted((rows.report(f"r{i}", at(rng.randint(0, 5))) for i in range(30)),
                     key=lambda r: (r["generated_at"], r["id"]), reverse=True)  # fmt: skip
    full = [i.id for i in pa.merge(reports, [], [], 0, 100).items]
    paged = []
    for offset in range(0, 30, 7):
        paged += [i.id for i in pa.merge(reports[: offset + 8], [], [], offset, 7).items]
    assert paged == full


def test_cited_reports_not_on_the_page_are_listed_once_for_the_day_lookup():
    page = pa.merge(
        [rows.report("r-1", at(1))],
        [rows.decision("d-1", at(2), ["r-1", "r-9"]), rows.decision("d-2", at(3), ["r-9", "r-8"])],
        [],
        0,
        10,
    )
    assert pa.missing_report_ids(page) == ["r-8", "r-9"]


# --- the rows -------------------------------------------------------------------------------


def view(page, times=None, **kw):
    return pa.activity_view(page, filters(**kw), times or {}, SGT)


def only(rows_, kind):
    return [r for r in rows_ if r.kind == kind]


def test_a_report_shows_agent_time_symbol_direction_conviction_size_sources_rationale():
    v = view(pa.merge([rows.report()], [], [], 0, 50))
    (r,) = [x.report for x in v.rows]
    assert (r.agent, r.symbol, r.direction, r.conviction, r.size) == (
        "research",
        "AAPL",
        "buy",
        "4",
        "5%",
    )
    assert r.at == "19 Oct 22:00 SGT (19 Oct 10:00 ET)" and r.anchor == "report-r-1"
    assert "<strong>Strong</strong>" in r.rationale.html and r.rationale.summary is None
    (s,) = r.sources
    assert 'href="https://news.example/a?b=1&amp;c=2"' in s.title
    assert (s.publisher, s.published_at, s.relevance) == (
        "Example Wire",
        "2026-10-18T12:00:00Z",
        "high",
    )


def test_a_no_action_report_has_no_symbol_conviction_or_sources():
    v = view(pa.merge([rows.no_action()], [], [], 0, 50))
    r = v.rows[0].report
    assert (r.symbol, r.conviction, r.size, r.sources) == ("", "", "", ())
    assert r.direction == "no_action"


def test_source_titles_with_unsafe_urls_or_markup_are_text_only():
    bad = [
        {"title": "<b>x</b>", "url": "javascript:alert(1)", "publisher": "<i>p</i>"},
        {"title": "ok", "url": "//evil.example"},
        "not a mapping",
        {"url": "https://a.example"},
    ]
    r = view(pa.merge([rows.report(sources=bad)], [], [], 0, 50)).rows[0].report
    assert len(r.sources) == 3
    assert "href" not in r.sources[0].title and "&lt;b&gt;x&lt;/b&gt;" in r.sources[0].title
    assert r.sources[0].publisher == "<i>p</i>"  # plain str, escaped by the template
    assert "href" not in r.sources[1].title
    assert "(untitled)" in r.sources[2].title and "https://a.example" in r.sources[2].title


def test_a_malformed_sources_value_does_not_fail_the_page():
    r = view(pa.merge([rows.report(sources={"a": 1})], [], [], 0, 50)).rows[0].report
    assert r.sources == ()


def test_long_text_gets_a_plain_summary():
    long = "word " * 400
    r = view(pa.merge([rows.report(rationale_md=long)], [], [], 0, 50)).rows[0].report
    assert r.rationale.summary and len(r.rationale.summary) <= 301
    assert type(r.rationale.summary) is str


def test_rationale_is_only_ever_rendered_through_the_restricted_markdown():
    hostile = "<script>alert(1)</script> [x](javascript:alert(1)) ![t](https://e.example/p.gif)"
    html = (
        view(pa.merge([rows.report(rationale_md=hostile)], [], [], 0, 50))
        .rows[0]
        .report.rationale.html
    )
    assert "<script" not in html and "<img" not in html and "href" not in html


def test_a_decision_shows_reasoning_quote_and_time():
    page = pa.merge([], [rows.decision(report_ids=[])], [], 0, 50)
    d = view(page).rows[0].decision
    assert d.symbol == "AAPL" and d.size == "5%" and d.direction == "buy"
    assert d.quote == "187.25 at 19 Oct 22:00 SGT (19 Oct 10:00 ET)"
    assert d.cited == () and "Both analysts agree." in d.reasoning.html


def test_a_cited_report_on_the_page_links_by_anchor_and_otherwise_by_its_day():
    other_day = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)  # 17 Oct 04:00 SGT
    page = pa.merge(
        [rows.report("r-1", at(1))], [rows.decision("d-1", at(2), ["r-1", "r-2", "r-3"])], [], 0, 50
    )
    v = view(page, {"r-2": other_day})
    d = only(v.rows, "decision")[0].decision
    assert [(c.label, c.href) for c in d.cited] == [
        ("report r-1", "#report-r-1"),
        ("report r-2", "/activity?from=2026-10-17&to=2026-10-17"),
        ("report r-3", None),
    ]


def test_runs_mark_problems_and_keep_detail_as_plain_text():
    page = pa.merge([], [], [rows.timed_out(), rows.skipped(), rows.run()], 0, 50)
    by_id = {r.run.agent + r.run.outcome: r.run for r in view(page).rows}
    timed = by_id["researchtimed out"]
    assert timed.problem and timed.outcome == "timed out" and timed.detail.full == "exit 124"
    assert timed.slot == "research_daily" and timed.started and timed.finished
    skipped = by_id["portfolio_managerskipped"]
    assert skipped.problem and skipped.started == "" and skipped.slot_at
    assert skipped.reason == "morning session"
    fine = by_id["researchsucceeded"]
    assert not fine.problem and fine.detail is None


def test_links_keep_every_filter_and_an_empty_page_says_so():
    f = filters(agent="research", problems=True, symbol="AAPL", day_from=date(2026, 10, 1),
                day_to=date(2026, 10, 9), page=2)  # fmt: skip
    v = pa.activity_view(pa.Page((), True), f, {}, SGT)
    assert (
        v.prev_url
        == "/activity?agent=research&problems=1&symbol=AAPL&from=2026-10-01&to=2026-10-09"
    )
    assert v.next_url == v.prev_url + "&page=3"
    assert v.empty_text == "No activity matches."
