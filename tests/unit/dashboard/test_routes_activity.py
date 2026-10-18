"""The /activity page (specs/014 tasks T022; US4 scenarios 1-6; SC-003)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest

from tests.fakes.dashboard_store import FakeStore, failing
from tests.unit.dashboard import activity_rows as rows
from tests.unit.dashboard.helpers import Built, build

HOSTILE = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "[click](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1)) [y]( javascript:alert(1)) [z](//evil.example/a)",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "![tracker](https://evil.example/p.gif)",
    '<a href="https://ok.example" onclick="x()">ok</a>',
    '"><svg onload=alert(1)>',
    "{{ 7*7 }} {% raw %}",
    "[ok](https://news.example/a?b=1&c=2)",
]
INTERNAL = re.compile(r"^(/(?!/)|#)")


def get(built: Built, query: str = ""):
    _, cookie = built.sign_in()
    return built.client().get("/activity" + query, headers=cookie)


def store(**kw) -> FakeStore:
    return FakeStore(**kw)


class Scan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def assert_inert(html: str, allow: frozenset[str] = frozenset()) -> None:
    scan = Scan()
    scan.feed(html)
    for tag, attrs in scan.tags:
        assert tag not in {"script", "img", "iframe", "svg", "object", "embed", "style"} - allow, (
            tag
        )
        for name, value in attrs.items():
            assert not name.startswith("on"), (tag, name)
            if name in {"href", "src", "action"}:
                assert INTERNAL.match(value) or value.lower().startswith(("http://", "https://"))


# --- the six scenarios ----------------------------------------------------------------------


def test_scenario_reports_from_both_analysts_with_every_field():
    built = build(store=store(report_rows=[rows.report("r-1"), rows.no_action("r-2", rows.AT)]))
    text = get(built).text
    for fragment in (
        "Report: research · AAPL · buy",
        "conviction 4",
        "suggested size 5%",
        "<strong>Strong</strong> quarter.",
        'href="https://news.example/a?b=1&amp;c=2" rel="nofollow noopener noreferrer"',
        "Example Wire",
        "2026-10-18T12:00:00Z",
        "(high)",
        "Report: opportunistic_identifier · no_action",
        "Nothing stands out.",
        'id="report-r-1"',
    ):
        assert fragment in text, fragment


def test_scenario_a_decision_links_to_its_two_cited_reports():
    built = build(
        store=store(
            report_rows=[rows.report("r-1"), rows.report("r-2", symbol="MSFT")],
            decision_rows=[rows.decision(report_ids=["r-1", "r-2"])],
        )
    )
    text = get(built).text
    assert "Decision: buy AAPL, target 5%" in text and "Both analysts agree." in text
    assert "quote 187.25 at 19 Oct 22:00 SGT" in text
    assert 'href="#report-r-1"' in text and 'href="#report-r-2"' in text


def test_a_cited_report_off_the_page_links_to_its_day():
    built = build(
        store=store(
            decision_rows=[rows.decision(report_ids=["r-9"])],
            report_times_map={"r-9": datetime(2026, 10, 16, 20, 0, tzinfo=UTC)},
        )
    )
    text = get(built).text
    assert 'href="/activity?from=2026-10-17&amp;to=2026-10-17"' in text
    assert [c for c in built.store.calls if c[0] == "report_times"] == [
        ("report_times", (("r-9",),))
    ]


def test_scenario_hostile_text_is_inert_on_the_real_page():
    joined = "\n\n".join(HOSTILE)
    source = {
        "title": '"><svg onload=alert(1)>',
        "url": "javascript:alert(1)",
        "publisher": HOSTILE[1],
    }
    built = build(
        store=store(
            report_rows=[rows.report(rationale_md=joined, sources=[source])],
            decision_rows=[rows.decision(reasoning_md=joined, report_ids=[])],
            run_rows=[rows.timed_out(detail=HOSTILE[0] + HOSTILE[1])],
        )
    )
    response = get(built)
    text = response.text
    assert response.status_code == 200
    assert_inert(text)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text
    # The literal survives, so the template never evaluated it. (No "49" check: random ids
    # and times on the page can contain those digits, which made this test flaky.)
    assert "{{ 7*7 }}" in text
    assert 'href="https://news.example/a?b=1&amp;c=2"' in text
    scan = Scan()
    scan.feed(text)
    attributes = [v for _, attrs in scan.tags for v in attrs.values() if v]
    assert not any("evil.example" in v or "p.gif" in v for v in attributes)
    assert not re.search(r"<[^>]*\son\w+\s*=", text, re.IGNORECASE)


def test_scenario_long_text_is_collapsed_with_the_rest_on_the_page():
    long = "A long rationale sentence. " * 100
    built = build(
        store=store(
            report_rows=[rows.report(rationale_md=long)],
            run_rows=[rows.timed_out(detail="d" * 900)],
        )
    )
    text = get(built).text
    assert text.count("<details><summary>") == 2
    assert "d" * 900 in text and "A long rationale sentence." in text


def test_scenario_filters_choose_the_queries_and_arguments():
    built = build(store=store())
    get(built, "?agent=research&symbol=aapl&from=2026-10-19&to=2026-10-19")
    calls = {name: args for name, args in built.store.calls if name != "state"}
    since = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)
    until = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
    assert set(calls) == {"reports", "report_times"}  # no decisions; no runs with a symbol
    assert calls["reports"] == ("research", "AAPL", since, until, 51)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", {"reports", "decisions", "runs"}),
        ("?agent=research", {"reports", "runs"}),
        ("?agent=opportunistic_identifier", {"reports", "runs"}),
        ("?agent=portfolio_manager", {"decisions", "runs"}),
        ("?agent=runs", {"runs"}),
        ("?problems=1", {"runs"}),
        ("?problems=1&symbol=AAPL", set()),
        ("?agent=bogus&problems=maybe", {"reports", "decisions", "runs"}),
    ],
)
def test_which_queries_run(query, expected):
    built = build(store=store())
    get(built, query)
    ran = {name for name, _ in built.store.calls} & {"reports", "decisions", "runs"}
    assert ran == expected


def test_the_run_queries_get_the_agent_and_problem_flag():
    built = build(store=store())
    get(built, "?agent=portfolio_manager")
    ((_, args),) = [c for c in built.store.calls if c[0] == "runs"]
    assert args[0] == "portfolio_manager" and args[3] is False
    built = build(store=store())
    get(built, "?problems=1")
    ((_, args),) = [c for c in built.store.calls if c[0] == "runs"]
    assert args[0] is None and args[3] is True


def test_scenario_a_timed_out_run_is_marked_and_shown_for_problems():
    built = build(store=store(run_rows=[rows.timed_out(), rows.run("u-9")]))
    text = get(built, "?problems=1").text
    assert '<article class="problem">' in text
    assert "<strong>timed out</strong>" in text and "slot research_daily" in text
    assert "exit 124" in text and "Due 19 Oct 22:00" in text and "started" in text


def test_a_skipped_pm_run_shows_its_detail_and_no_start_time():
    text = get(build(store=store(run_rows=[rows.skipped()]))).text
    assert "<strong>skipped</strong>" in text and "trading paused" in text
    assert "started" not in text


# --- paging ---------------------------------------------------------------------------------


def test_each_query_is_asked_for_offset_plus_page_size_plus_one():
    built = build(store=store())
    get(built, "?page=3")
    for name in ("reports", "decisions", "runs"):
        ((_, args),) = [c for c in built.store.calls if c[0] == name]
        assert args[-1] == 2 * 50 + 50 + 1


def test_pages_merge_newest_first_and_link_onward_and_back():
    reports = [
        rows.report(f"r-{i:03d}", rows.AT.replace(minute=0) - (i * rows.AT.resolution))
        for i in range(60)
    ]
    built = build(store=store(report_rows=reports))
    text = get(built).text
    assert text.count("<article") == 50 and 'href="/activity?page=2">Older' in text
    assert text.index("report-r-000") < text.index("report-r-049")
    built = build(store=store(report_rows=reports))
    text = get(built, "?page=2").text
    assert text.count("<article") == 10 and 'href="/activity">Newer' in text and "Older" not in text


def test_empty_result_and_page_past_the_end_are_lists_not_errors():
    response = get(build(store=store()), "?page=9&symbol=ZZZ")
    assert response.status_code == 200 and "No activity matches." in response.text


def test_bad_query_values_are_ignored():
    response = get(
        build(store=store()), "?agent=%3Cscript%3E&problems=%3C&symbol=%27&from=x&to=y&page=-5"
    )
    assert response.status_code == 200 and "<script" not in response.text


def test_the_form_echoes_the_filters_escaped():
    text = get(build(store=store()), "?agent=research&symbol=aapl&problems=1&from=2026-10-01").text
    assert '<option value="research" selected>' in text
    assert 'name="symbol" value="AAPL"' in text and "checked" in text
    assert 'name="from" value="2026-10-01"' in text


def test_the_shell_database_failure_and_session():
    ok = get(build(store=store())).text
    assert "Read at " in ok and 'aria-current="page">Activity</a>' in ok
    assert get(build(store=failing())).status_code == 503
    built = build(store=store(report_rows=[rows.report()]))
    response = built.client(follow_redirects=False).get("/activity")
    assert response.status_code == 303 and built.store.read_calls == 0
