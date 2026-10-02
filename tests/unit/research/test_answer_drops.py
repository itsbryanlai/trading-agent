"""Every drop rule (specs/007-research-agent research R6; FR-007, FR-009; spec US2)."""

from __future__ import annotations

import json

import pytest

from tests.unit.research.support import THU_0830, article, config, proposal
from trading_agent.research.answer import check
from trading_agent.research.selection import select

SYMBOLS = {
    "AAPL": "APPLE INC",
    "MSFT": "MICROSOFT CORP",
    "NVDA": "NVIDIA CORP",
    "BRK.B": "BERKSHIRE HATHAWAY INC-CL B",
}


def _articles():
    chosen = select(
        [article("apple", related=("AAPL",)), article("micro", related=("MSFT",))],
        {"NVDA": [article("nvidia")]},
        THU_0830,
        config(watchlist=("NVDA",)),
    )
    return {a.id: a for a in chosen.articles}  # A1 AAPL, A2 MSFT, A3 NVDA (its feed)


def run_text(text, open_reports=()):
    return check(text, _articles(), SYMBOLS, open_reports, 2000)


def run(items, open_reports=()):
    return run_text(json.dumps({"proposals": items}), open_reports)


def only_drop(items, open_reports=()):
    checked = run(items, open_reports)
    assert checked.reports == ()
    (drop,) = checked.drops
    return drop


def with_field(name, value, base=None):
    item = base or proposal("AAPL", ids=["A1"])
    item[name] = value
    return item


@pytest.mark.parametrize(
    "item",
    [
        "AAPL",
        ["AAPL"],
        None,
        {k: v for k, v in proposal().items() if k != "rationale"},
        {**proposal(), "extra": 1},
        with_field("rationale", ""),
        with_field("rationale", 5),
    ],
)
def test_malformed_answer(item):
    drop = only_drop([item])
    assert (drop.index, drop.symbol, drop.reason) == (0, None, "malformed_answer")


@pytest.mark.parametrize("symbol", ["aapl", "TOOLONG", "", 123, "BRK/B", None, "AAPL "])
def test_invalid_symbol(symbol):
    drop = only_drop([with_field("symbol", symbol)])
    assert (drop.symbol, drop.reason) == (None, "invalid_symbol")


def test_unlisted_symbol():
    drop = only_drop([proposal("ZZZZ", ids=["A1"])])
    assert (drop.symbol, drop.reason) == ("ZZZZ", "unlisted_symbol")


@pytest.mark.parametrize("direction", ["hold", "BUY", "short", None, 1])
def test_invalid_direction(direction):
    assert only_drop([with_field("direction", direction)]).reason == "invalid_direction"


@pytest.mark.parametrize("conviction", [0, 6, 2.5, "3", True, None, 3.0])
def test_invalid_conviction(conviction):
    assert only_drop([with_field("conviction", conviction)]).reason == "invalid_conviction"


@pytest.mark.parametrize(
    ("direction", "size"),
    [
        ("buy", 0),
        ("buy", -1),
        ("buy", 0.0004),  # rounds down to 0.000
        ("sell", -0.001),
        ("buy", 100.001),
        ("sell", 100.001),
        ("buy", "5"),
        ("buy", True),
        ("buy", None),
    ],
)
def test_invalid_size(direction, size):
    item = with_field("suggested_size_pct", size, proposal("AAPL", direction, ids=["A1"]))
    assert only_drop([item]).reason == "invalid_size"


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_sizes_from_a_lenient_encoder(raw):
    text = json.dumps({"proposals": [proposal("AAPL", ids=["A1"])]}).replace(
        '"suggested_size_pct": 4', f'"suggested_size_pct": {raw}'
    )
    (drop,) = run_text(text).drops
    assert drop.reason == "invalid_size"


@pytest.mark.parametrize("ids", [[], "A1", [1], None])
def test_no_citation(ids):
    assert only_drop([with_field("article_ids", ids)]).reason == "no_citation"


def test_unknown_citation():
    assert only_drop([proposal("AAPL", ids=["A1", "A99"])]).reason == "unknown_citation"


def test_uncited_symbol_when_no_cited_article_is_about_it():
    # A listed MSFT citing only the AAPL-tagged article (spec Clarifications, analyze S1).
    drop = only_drop([proposal("MSFT", ids=["A1"])])
    assert (drop.symbol, drop.reason) == ("MSFT", "uncited_symbol")


def test_a_tagged_article_or_the_symbols_own_feed_makes_it_relevant():
    checked = run([proposal("MSFT", ids=["A1", "A2"]), proposal("NVDA", ids=["A3"])])
    assert [r.symbol for r in checked.reports] == ["MSFT", "NVDA"]


def test_duplicate_symbol_even_with_another_direction():
    checked = run([proposal("AAPL", "buy", ids=["A1"]), proposal("AAPL", "sell", ids=["A1"])])
    assert [r.direction for r in checked.reports] == ["buy"]
    (drop,) = checked.drops
    assert (drop.index, drop.symbol, drop.reason) == (1, "AAPL", "duplicate_symbol")


def test_an_invalid_first_proposal_doesnt_block_a_valid_second_one():
    checked = run([proposal("AAPL", conviction=9, ids=["A1"]), proposal("AAPL", ids=["A1"])])
    assert [r.symbol for r in checked.reports] == ["AAPL"]
    assert [d.reason for d in checked.drops] == ["invalid_conviction"]


def test_already_open_same_direction_is_dropped_other_direction_is_written():
    open_reports = [("AAPL", "buy")]
    assert only_drop([proposal("AAPL", "buy", ids=["A1"])], open_reports).reason == "already_open"
    checked = run([proposal("AAPL", "sell", size=1, ids=["A1"])], open_reports)
    assert [(r.symbol, r.direction) for r in checked.reports] == [("AAPL", "sell")]


def test_a_mix_writes_the_valid_ones_and_drops_the_rest():
    checked = run(
        [proposal("AAPL", ids=["A1"]), proposal("ZZZZ", ids=["A1"]), proposal("MSFT", ids=["A2"])]
    )
    assert [r.symbol for r in checked.reports] == ["AAPL", "MSFT"]
    assert [(d.index, d.reason) for d in checked.drops] == [(1, "unlisted_symbol")]
    assert checked.received == 3


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"proposals": [], "note": "x"}',
        '{"proposals": {}}',
        '{"answers": []}',
        "null",
        "",
    ],
)
def test_an_answer_not_in_the_required_shape_is_unusable(text):
    checked = run_text(text)
    assert checked.unusable and checked.reports == () and checked.drops == ()
