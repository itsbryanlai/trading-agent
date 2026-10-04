"""answer.check, the drop rules (specs/008-portfolio-manager research P8; spec US2; FR-009).

One row per drop reason, in research P8's order, then the cases that must be accepted
and the answer as a whole."""

from __future__ import annotations

import pytest

from tests.unit.portfolio_manager.answer_support import check_items, check_text, item
from trading_agent.portfolio_manager import answer

# (name, item, reason). The held weights are MSFT 15 and NVDA 5; AAPL and TSLA are not held.
DROPPED = [
    ("not an object", "AAPL", "malformed_decision"),
    ("a missing field", {"symbol": "AAPL", "direction": "buy"}, "malformed_decision"),
    ("an extra field", {**item(), "extra": 1}, "malformed_decision"),
    ("a size given as text", item(target="4"), "malformed_decision"),
    ("a size given as a boolean", item(target=True), "malformed_decision"),
    ("a symbol that isn't text", item(symbol=7), "malformed_decision"),
    ("ids that aren't a list", {**item(), "report_ids": "R1"}, "malformed_decision"),
    ("an id that isn't text", {**item(), "report_ids": [1]}, "malformed_decision"),
    ("reasoning that isn't text", item(reasoning=None), "malformed_decision"),
    ("a symbol not given", item("XYZ", ids=("R1",)), "unknown_symbol"),
    ("a lower-case symbol", item("aapl"), "unknown_symbol"),
    ("direction HOLD", item(direction="HOLD"), "invalid_direction"),
    ("direction Buy", item(direction="Buy"), "invalid_direction"),
    ("direction short", item(direction="short"), "invalid_direction"),
    ("an empty direction", item(direction=""), "invalid_direction"),
    ("no report ids", item(ids=()), "no_citation"),
    ("an unknown id", item(ids=("R99",)), "unknown_citation"),
    ("a database id", item(ids=("db-aapl-1",)), "unknown_citation"),
    ("an id given for another symbol", item("AAPL", ids=("R3",)), "unknown_citation"),
    ("a good id and one for another symbol", item("AAPL", ids=("R1", "R6")), "unknown_citation"),
    ("a buy citing only a sell report", item("MSFT", "buy", 20, ids=("R3",)), "unbacked_buy"),
    ("a conflict cited one side (buy)", item("NVDA", "buy", 8, ids=("R4",)), "one_sided_conflict"),
    (
        "a conflict cited one side (sell)",
        item("NVDA", "sell", 1, ids=("R5",)),
        "one_sided_conflict",
    ),
    ("a hold on a conflict, one side", item("NVDA", "hold", 0, ids=("R4",)), "one_sided_conflict"),
    ("a size of -1", item(target=-1), "invalid_size"),
    ("a size of 100.001", item(target=100.001), "invalid_size"),
    ("a size of NaN", item(target=float("nan")), "invalid_size"),
    ("a size of infinity", item(target=float("inf")), "invalid_size"),
    ("a buy at 0", item(target=0), "invalid_size"),
    ("a buy at 0.0004", item(target=0.0004), "invalid_size"),
    ("a tiny sell, not a full exit", item("MSFT", "sell", 0.0004, ids=("R3",)), "invalid_size"),
    ("a buy at the current weight", item("NVDA", "buy", 5, ids=("R4", "R5")), "contradicts"),
    ("a buy below the current weight", item("NVDA", "buy", 4, ids=("R4", "R5")), "contradicts"),
    ("a sell at the current weight", item("MSFT", "sell", 15, ids=("R3",)), "contradicts"),
    ("a sell above the current weight", item("MSFT", "sell", 20, ids=("R3",)), "contradicts"),
    ("a sell on a symbol not held", item("TSLA", "sell", 0, ids=("R6",)), "contradicts"),
]
_REASONS = {"contradicts": "direction_contradicts_target"}


@pytest.mark.parametrize(("name", "bad", "reason"), DROPPED, ids=[d[0] for d in DROPPED])
def test_each_bad_item_is_dropped_with_its_own_reason(name, bad, reason):
    checked = check_items(bad)
    assert checked.decisions == ()
    assert [d.reason for d in checked.drops] == [_REASONS.get(reason, reason)]
    assert checked.received == 1
    assert not checked.unusable


ACCEPTED = [
    ("a buy citing two buy reports", item("AAPL", "buy", 4, ids=("R1", "R2"))),
    ("a buy citing a buy and a sell report", item("NVDA", "buy", 8, ids=("R4", "R5"))),
    ("a sell citing both sides of a conflict", item("NVDA", "sell", 1, ids=("R5", "R4"))),
    ("a hold citing both sides of a conflict", item("NVDA", "hold", 0, ids=("R4", "R5"))),
    ("a sell to zero", item("MSFT", "sell", 0, ids=("R3",))),
    ("a partial sell", item("MSFT", "sell", 10, ids=("R3",))),
    ("a buy just above the current weight", item("NVDA", "buy", 5.001, ids=("R4", "R5"))),
    ("a hold whatever its size says", item("MSFT", "hold", 55, ids=("R3",))),
    ("a hold with a size of 0", item("AAPL", "hold", 0, ids=("R1",))),
]


@pytest.mark.parametrize(("name", "good"), ACCEPTED, ids=[a[0] for a in ACCEPTED])
def test_valid_items_are_accepted(name, good):
    checked = check_items(good)
    assert checked.drops == ()
    assert len(checked.decisions) == 1


@pytest.mark.parametrize("size", [-5, 1000, 0.0004, float("nan")])
def test_a_holds_size_is_ignored_even_when_it_is_not_a_valid_size(size):
    (d,) = check_items(item("MSFT", "hold", size, ids=("R3",))).decisions
    assert (d.direction, d.size_pct) == ("hold", 15)  # the current weight, not the model's


def test_a_hold_on_an_unheld_symbol_is_written_at_zero():
    (d,) = check_items(item("AAPL", "hold", 9, ids=("R1",))).decisions
    assert d.size_pct == 0


def test_a_buy_citing_a_buy_and_a_sell_report_keeps_both_links():
    (d,) = check_items(item("NVDA", "buy", 8, ids=("R4", "R5"))).decisions
    assert d.report_ids == ("db-nvda-b", "db-nvda-s")


def test_a_second_decision_on_the_same_symbol_is_a_duplicate():
    checked = check_items(item("AAPL", "buy", 4), item("AAPL", "buy", 6, ids=("R2",)))
    assert [d.size_pct for d in checked.decisions] == [4]
    assert [(d.index, d.symbol, d.reason) for d in checked.drops] == [
        (1, "AAPL", "duplicate_symbol")
    ]


def test_a_hold_then_a_buy_on_one_symbol_is_a_duplicate():
    checked = check_items(item("AAPL", "hold", 0), item("AAPL", "buy", 4))
    assert [d.direction for d in checked.decisions] == ["hold"]
    assert [d.reason for d in checked.drops] == ["duplicate_symbol"]


def test_an_invalid_first_proposal_does_not_make_the_second_a_duplicate():
    checked = check_items(item("AAPL", "buy", 0), item("AAPL", "buy", 4))
    assert [d.reason for d in checked.drops] == ["invalid_size"]
    assert len(checked.decisions) == 1


def test_valid_proposals_survive_beside_invalid_ones():
    checked = check_items(
        item("AAPL", "buy", 4),
        item("XYZ", "buy", 100, ids=("R1",)),
        item("MSFT", "sell", 0, ids=("R3",)),
        item("TSLA", "buy", 3, ids=("R3",)),
        "junk",
    )
    assert [d.symbol for d in checked.decisions] == ["AAPL", "MSFT"]
    assert [(d.index, d.symbol, d.reason) for d in checked.drops] == [
        (1, "XYZ", "unknown_symbol"),
        (3, "TSLA", "unknown_citation"),
        (4, None, "malformed_decision"),
    ]
    assert checked.received == 5


def test_the_first_failing_rule_in_the_order_of_research_p8_names_the_drop():
    # An unknown symbol with everything else wrong is still just `unknown_symbol`.
    assert [d.reason for d in check_items(item("XYZ", "short", -5, ids=())).drops] == [
        "unknown_symbol"
    ]
    # A bad direction comes before an empty citation, which comes before a bad size.
    assert [d.reason for d in check_items(item(direction="x", ids=(), target=-1)).drops] == [
        "invalid_direction"
    ]
    assert [d.reason for d in check_items(item(ids=(), target=-1)).drops] == ["no_citation"]
    # An unbacked buy is named before a bad size.
    assert [d.reason for d in check_items(item("MSFT", "buy", -1, ids=("R3",))).drops] == [
        "unbacked_buy"
    ]
    # A conflict cited on one side is named before a bad size, and after a bad citation.
    assert [d.reason for d in check_items(item("NVDA", "sell", -1, ids=("R5",))).drops] == [
        "one_sided_conflict"
    ]
    assert [d.reason for d in check_items(item("NVDA", "sell", -1, ids=("R5", "R1"))).drops] == [
        "unknown_citation"
    ]


def test_the_drop_reasons_are_the_closed_set_of_the_contract():
    assert set(answer.DROP_REASONS) == {
        "malformed_decision",
        "unknown_symbol",
        "invalid_direction",
        "no_citation",
        "unknown_citation",
        "unbacked_buy",
        "one_sided_conflict",
        "invalid_size",
        "direction_contradicts_target",
        "duplicate_symbol",
    }


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "",
        "[]",
        "null",
        '"decisions"',
        "{}",
        '{"decisions": {}}',
        '{"decisions": "none"}',
        '{"decisions": null}',
        '{"decisions": [], "extra": 1}',
        '{"proposals": []}',
    ],
)
def test_an_answer_that_is_not_a_decisions_list_is_unusable(text):
    checked = check_text(text)
    assert checked.unusable
    assert checked.decisions == ()


def test_an_empty_decisions_list_is_a_normal_answer():
    checked = check_text('{"decisions": []}')
    assert not checked.unusable
    assert (checked.decisions, checked.drops, checked.received) == ((), (), 0)
