"""The valid path of the checker (specs/007-research-agent research R6; FR-008, FR-010)."""

from __future__ import annotations

import json
from decimal import Decimal

from tests.unit.research.support import THU_0830, article, config, proposal
from trading_agent.research.answer import ANSWER_SCHEMA, ELLIPSIS, check
from trading_agent.research.selection import select

SYMBOLS = {"AAPL": "APPLE INC", "MSFT": "MICROSOFT CORP", "NVDA": "NVIDIA CORP"}


def _articles():
    chosen = select(
        [article("apple", related=("AAPL",)), article("micro", related=("MSFT",))],
        {},
        THU_0830,
        config(),
    )
    return {a.id: a for a in chosen.articles}


def run(proposals, *, open_reports=(), cap=2000, articles=None):
    arts = articles or _articles()
    text = json.dumps({"proposals": proposals})
    return check(text, arts, SYMBOLS, open_reports, cap)


def test_two_valid_proposals_become_two_reports():
    checked = run([proposal("AAPL", "buy", 4, 5, ["A1"]), proposal("MSFT", "sell", 2, 1, ["A2"])])
    assert not checked.unusable and checked.drops == () and checked.received == 2
    aapl, msft = checked.reports
    assert (aapl.symbol, aapl.direction, aapl.conviction, aapl.size) == (
        "AAPL",
        "buy",
        4,
        Decimal(5),
    )
    assert (msft.symbol, msft.direction) == ("MSFT", "sell")


def test_sources_come_from_the_articles_never_from_the_model():
    arts = _articles()
    sneaky = proposal("AAPL", ids=["A1"], rationale="See https://evil.example/fake-source")
    (report,) = run([sneaky], articles=arts).reports
    a1 = arts["A1"]
    assert report.sources == (
        {
            "title": a1.title,
            "url": a1.url,
            "publisher": a1.publisher,
            "published_at": a1.published_at.isoformat(),
        },
    )


def test_sources_follow_citation_order_without_duplicates():
    arts = _articles()
    (report,) = run([proposal("AAPL", ids=["A2", "A1", "A2"])], articles=arts).reports
    assert [s["url"] for s in report.sources] == [arts["A2"].url, arts["A1"].url]


def test_size_is_rounded_down_to_three_places():
    (report,) = run([proposal(size=4.56789)]).reports
    assert report.size == Decimal("4.567")


def test_a_sell_may_target_zero():
    (report,) = run([proposal("MSFT", "sell", size=0, ids=["A2"])]).reports
    assert report.size == Decimal("0.000")


def test_the_rationale_is_trimmed_and_capped_including_the_ellipsis():
    (short,) = run([proposal(rationale="  fine  ")]).reports
    assert short.rationale == "fine"
    (long,) = run([proposal(rationale="x" * 500)], cap=200).reports
    assert len(long.rationale) == 200 and long.rationale.endswith(ELLIPSIS)


def test_an_empty_list_is_a_usable_answer_with_nothing_in_it():
    checked = run([])
    assert (checked.unusable, checked.reports, checked.received) == (False, (), 0)


def test_the_schema_is_strict_at_both_levels():
    assert ANSWER_SCHEMA["additionalProperties"] is False
    assert ANSWER_SCHEMA["required"] == ["proposals"]
    item = ANSWER_SCHEMA["properties"]["proposals"]["items"]
    assert item["additionalProperties"] is False
    assert (
        set(item["required"])
        == set(item["properties"])
        == {
            "symbol",
            "direction",
            "conviction",
            "suggested_size_pct",
            "rationale",
            "article_ids",
        }
    )
    assert item["properties"]["direction"]["enum"] == ["buy", "sell"]
