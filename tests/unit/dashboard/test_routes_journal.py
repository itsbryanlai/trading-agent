"""The /journal page (specs/014 tasks T024; US5 scenarios 1-5)."""

from __future__ import annotations

import copy
import re
from datetime import date

from tests.fakes.dashboard_store import FakeStore, failing
from tests.unit.dashboard import attribution_fixtures as fx
from tests.unit.dashboard.attribution_fixtures import journal_row
from tests.unit.dashboard.helpers import Built, build
from tests.unit.dashboard.test_routes_activity import assert_inert


def rows(count=5, **agents) -> list[dict]:
    out, day = [], date(2026, 10, 5)
    while len(out) < count:
        if day.weekday() < 5:
            iso = day.isoformat()
            books = agents or {"research": fx.book("2026-10-05", "100")}
            out.append(journal_row(iso, fx.day(iso, "0.010000", agents=copy.deepcopy(books))))
        day = date.fromordinal(day.toordinal() + 1)
    return list(reversed(out))  # the page query is newest first


def make(page_rows, range_rows=None) -> Built:
    return build(
        store=FakeStore(
            journal_rows=page_rows,
            journal_range_rows=list(reversed(page_rows)) if range_rows is None else range_rows,
        )
    )


def get(built: Built, query=""):
    _, cookie = built.sign_in()
    return built.client().get("/journal" + query, headers=cookie)


def test_scenario_each_day_shows_equity_summary_and_each_books_figures():
    obj = fx.day("2026-10-09", "0.012345")
    obj["agents"]["research"]["holdings"]["AAPL"]["weight_pct"] = "5.000000"
    built = make([journal_row("2026-10-09", obj, summary="**Up** a bit.", close="101234.5")])
    text = get(built).text
    for fragment in (
        "<h3>Fri 9 Oct</h3>",
        "Equity 100,000.00 to 101,234.50",
        "account +1.23%",
        "<strong>Up</strong> a bit.",
        "research: index 100.00, day +0.00%",
        '<td>AAPL</td><td class="num">5.00%</td><td class="num">229.15</td>',
    ):
        assert fragment in text, fragment


def test_scenario_missed_sessions_and_unpriced_holdings_are_named():
    obj = fx.day("2026-10-09", "0.01", missed=["2026-10-07", "2026-10-08"])
    obj["agents"]["research"].update(unpriced=["MU"], skipped_targets=["NVDA"])
    text = get(make([journal_row("2026-10-09", obj)])).text
    assert "Missed sessions: Wed 7 Oct, Thu 8 Oct." in text
    assert "Carried at last price: MU." in text and "not entered for want of a price: NVDA" in text


def test_scenario_an_unknown_version_does_not_fail_the_page():
    good = journal_row("2026-10-08", fx.day("2026-10-08", "0.01"), summary="Fine day.")
    bad = journal_row("2026-10-09", fx.example(schema_version=2), summary="Odd day.")
    response = get(make([bad, good]))
    assert response.status_code == 200
    assert "Odd day." in response.text and "Fine day." in response.text
    assert "Attribution can&#39;t be displayed for this day." in response.text


def test_scenario_a_month_has_a_line_per_analyst_and_the_account_and_summed_usage():
    books = {
        "research": fx.book("2026-10-05", "100", written=2),
        "opportunistic_identifier": fx.book("2026-10-05", "100", written=1),
    }
    built = make(rows(20, **books))
    text = get(built, "?range=1m").text
    assert text.count("<polyline") == 3  # account, research, opportunistic_identifier
    for name in ("account", "research", "opportunistic_identifier"):
        assert f"</span>{name}: 100.00</li>" in text or f"</span>{name}:" in text
    assert re.search(r"<td>research</td><td class=\"num\">40</td>", text)  # 20 rows x written 2
    assert re.search(r"<td>opportunistic_identifier</td><td class=\"num\">20</td>", text)


def test_scenario_an_analyst_that_starts_mid_range_has_a_shorter_line():
    page = rows(5)
    for row in page:
        if row["trading_day"] >= date(2026, 10, 7):
            row["per_agent_attribution"]["agents"]["opportunistic_identifier"] = fx.book(
                "2026-10-07", "100"
            )
    text = get(make(page)).text
    assert text.count("<polyline") == 3
    late = re.search(r'<g class="series-1">\s*<polyline points="([^"]+)"', text).group(1)
    assert len(late.split()) == 3


def test_missed_sessions_are_drawn_as_markers():
    page = rows(5)
    page[1]["per_agent_attribution"]["missed_sessions"] = ["2026-10-07"]
    text = get(make(page)).text
    assert text.count('<line class="marker"') == 1 and "Vertical marks are missed sessions." in text


def test_the_range_chooses_the_since_date_and_both_queries():
    built = make(rows(2))
    get(built, "?range=1w")
    calls = {name: args for name, args in built.store.calls if name.startswith("journal")}
    assert calls["journal_page"][1:] == (51, 0)
    assert (
        calls["journal_page"][0] == calls["journal_range"][0] == date(2026, 10, 12)
    )  # clock: Mon 19 Oct


def test_one_day_is_treated_as_one_week_and_all_has_no_lower_bound():
    built = make(rows(2))
    get(built, "?range=1d")
    assert dict(built.store.calls)["journal_range"] == (date(2026, 10, 12),)
    built = make(rows(2))
    get(built, "?range=all")
    assert dict(built.store.calls)["journal_range"] == (None,)
    assert get(make(rows(2)), "?range=1d").text.count('aria-current="page">1w</a>') == 1


def test_the_default_range_is_one_month_and_bad_values_are_ignored():
    built = make(rows(2))
    text = get(built, "?range=%3Cscript%3E&page=zzz").text
    assert dict(built.store.calls)["journal_range"] == (date(2026, 9, 19),)
    assert 'aria-current="page">1m</a>' in text and "<script" not in text


def test_paging_asks_for_one_extra_row_and_links_onward():
    built = make(rows(5) * 12)  # 60 rows
    text = get(built).text
    assert text.count("<article") == 50 and 'href="/journal?page=2">Older' in text
    text = get(make(rows(5) * 12), "?page=2").text
    assert text.count("<article") == 10 and 'href="/journal">Newer' in text


def test_nothing_in_the_range_says_so():
    text = get(make([], [])).text
    assert text.count("No journal rows for this range.") == 2  # the chart and the days
    assert "No usage to total" in text


def test_hostile_text_is_inert_on_the_real_page():
    obj = fx.day("2026-10-09", "0.01")
    obj["agents"]["research"]["holdings"]['"><img src=x onerror=alert(1)>'] = {
        "weight_pct": "1", "ref_price": "1", "support_session": None,
    }  # fmt: skip
    obj["agents"]["<script>alert(1)</script>"] = copy.deepcopy(obj["agents"]["research"])
    obj["agents"]["research"]["unpriced"] = ["<svg onload=alert(1)>"]
    summary = (
        "<script>alert(1)</script> [x](javascript:alert(1)) "
        "![t](https://e.example/p.gif) <img src=x onerror=1>"
    )
    text = get(make([journal_row("2026-10-09", obj, summary=summary)])).text
    assert_inert(text, allow=frozenset({"svg"}))  # the chart is the page's one inline svg
    assert text.count("<svg") == 1
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text and "&lt;svg onload=alert(1)&gt;" in text
    assert not re.search(r"<[^>]*\son\w+\s*=", text, re.IGNORECASE)


def test_the_shell_database_failure_and_session():
    ok = get(make(rows(1))).text
    assert "Read at " in ok and 'aria-current="page">Journal</a>' in ok
    assert get(build(store=failing())).status_code == 503
    built = make(rows(1))
    response = built.client(follow_redirects=False).get("/journal")
    assert response.status_code == 303 and built.store.read_calls == 0
