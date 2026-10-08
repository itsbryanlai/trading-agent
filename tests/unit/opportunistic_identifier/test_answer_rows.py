"""The valid path of `answer.check` and `answer.rows`: a model proposal becomes a report
row with code-built sources (specs/011 research O8, O9; spec FR-013, FR-015)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.unit.opportunistic_identifier.support import FAKE_KEY, FRESH, NOW, name_data
from trading_agent.opportunistic_identifier import answer as a
from trading_agent.opportunistic_identifier import finnhub

THURSDAY = date(2026, 10, 8)
EARLY_CLOSE = date(2026, 11, 27)
DATA = {"AAA": name_data("AAA"), "BBB": name_data("BBB")}


def proposal(symbol="AAA", **changes) -> dict:
    item = {
        "symbol": symbol,
        "direction": "buy",
        "conviction": 4,
        "suggested_size_pct": 5,
        "rationale": "Cheap on earnings after a 5% fall.",
    }
    item.update(changes)
    return item


def answer(*items) -> str:
    return json.dumps({"proposals": list(items)})


def check(text, shortlist=("AAA", "BBB"), open_symbols=(), cap=2000):
    return a.check(text, shortlist, frozenset(open_symbols), cap)


def test_a_valid_answer_for_two_shortlisted_names_gives_two_buy_rows():
    checked = check(answer(proposal("AAA", conviction=5), proposal("BBB", conviction=2)))
    assert (checked.received, checked.unusable, checked.drops) == (2, False, ())
    rows = a.rows(checked, DATA, THURSDAY)
    assert [(r.symbol, r.direction, r.conviction) for r in rows] == [
        ("AAA", "buy", 5),
        ("BBB", "buy", 2),
    ]
    assert all(r.rationale_md == "Cheap on earnings after a 5% fall." for r in rows)


@pytest.mark.parametrize(
    ("given", "stored"),
    [(12.34567, "12.345"), (12.3459, "12.345"), (5, "5.000"), (0.001, "0.001"), (100, "100.000")],
)
def test_the_size_is_a_decimal_rounded_down_to_three_places(given, stored):
    (row,) = a.rows(check(answer(proposal(suggested_size_pct=given))), DATA, THURSDAY)
    assert row.suggested_size_pct == Decimal(stored)
    assert isinstance(row.suggested_size_pct, Decimal)


def test_the_rationale_is_cleaned_and_then_cut_with_an_ellipsis():
    text = answer(proposal(rationale="Cheap\x00 and\ud800 good. " + "x" * 300))
    (row,) = a.rows(check(text, cap=200), DATA, THURSDAY)
    assert len(row.rationale_md) == 200 and row.rationale_md.endswith("…")
    assert row.rationale_md.startswith("Cheap and good. ")
    assert "\x00" not in row.rationale_md


def test_a_rationale_within_the_cap_is_kept_whole():
    (row,) = a.rows(check(answer(proposal(rationale="  short thesis  "))), DATA, THURSDAY)
    assert row.rationale_md == "short thesis"


def test_a_row_expires_at_the_trading_days_close():
    (row,) = a.rows(check(answer(proposal())), DATA, THURSDAY)
    assert row.expires_at == datetime(2026, 10, 8, 20, 0, tzinfo=UTC)


def test_an_early_close_day_expires_at_the_early_close():
    (row,) = a.rows(check(answer(proposal())), DATA, EARLY_CLOSE)
    assert row.expires_at == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


def test_each_row_has_exactly_the_three_code_built_sources():
    (row,) = a.rows(check(answer(proposal())), DATA, THURSDAY)
    base = "https://finnhub.io/api/v1"
    assert row.sources == [
        {
            "title": "Finnhub quote for AAA",
            "url": f"{base}/quote?symbol=AAA",
            "publisher": "Finnhub",
            "published_at": FRESH.isoformat(),
            "relevance": "primary",
        },
        {
            "title": "Finnhub company profile for AAA",
            "url": f"{base}/stock/profile2?symbol=AAA",
            "publisher": "Finnhub",
            "published_at": NOW.isoformat(),
            "relevance": "primary",
        },
        {
            "title": "Finnhub basic financials for AAA",
            "url": f"{base}/stock/metric?symbol=AAA&metric=all",
            "publisher": "Finnhub",
            "published_at": NOW.isoformat(),
            "relevance": "primary",
        },
    ]


def test_the_source_base_url_is_the_adapters():
    assert a.SOURCE_BASE_URL == finnhub.BASE_URL


def test_a_missing_quote_time_falls_back_to_the_fetch_time():
    data = {"AAA": replace(DATA["AAA"], quote=replace(DATA["AAA"].quote, timestamp=None))}
    (row,) = a.rows(check(answer(proposal())), data, THURSDAY)
    assert row.sources[0]["published_at"] == NOW.isoformat()


def test_no_source_field_holds_a_key_or_anything_from_the_model():
    text = answer(proposal(rationale=f"see https://evil.example/x?k={FAKE_KEY}"))
    (row,) = a.rows(check(text), DATA, THURSDAY)
    for source in row.sources:
        assert set(source) == {"title", "url", "publisher", "published_at", "relevance"}
        assert all(FAKE_KEY not in value and "evil" not in value for value in source.values())


def test_an_empty_proposal_list_gives_no_rows():
    checked = check(answer())
    assert (checked.received, checked.unusable, checked.reports, checked.drops) == (
        0,
        False,
        (),
        (),
    )
    assert a.rows(checked, DATA, THURSDAY) == []


def test_the_schema_is_generated_from_the_contracts_table():
    item = a.ANSWER_SCHEMA["properties"]["proposals"]["items"]
    assert set(item["properties"]) == {
        "symbol",
        "direction",
        "conviction",
        "suggested_size_pct",
        "rationale",
    }
    assert item["required"] == list(item["properties"])
    assert item["additionalProperties"] is False
    assert item["properties"]["direction"] == {"type": "string", "enum": ["buy"]}
    assert a.ANSWER_SCHEMA["required"] == ["proposals"]
    assert a.ANSWER_SCHEMA["additionalProperties"] is False
