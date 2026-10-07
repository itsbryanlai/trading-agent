"""Every drop rule, one reason each, in the contract's order (specs/011 research O8; spec
FR-012). The reason a proposal gets depends on the order of the checks: shape, then
not_shortlisted, invalid_direction, invalid_conviction, invalid_size, duplicate_symbol,
already_open."""

from __future__ import annotations

import json

import pytest

from trading_agent.opportunistic_identifier import answer as a

SHORTLIST = ("AAA", "BBB", "MSFT")


def proposal(**changes) -> dict:
    item = {
        "symbol": "AAA",
        "direction": "buy",
        "conviction": 3,
        "suggested_size_pct": 5,
        "rationale": "Cheap after its fall.",
    }
    item.update(changes)
    return item


def check(*items, open_symbols=(), text=None):
    return a.check(
        text if text is not None else json.dumps({"proposals": list(items)}),
        SHORTLIST,
        frozenset(open_symbols),
        2000,
    )


def only_drop(item) -> a.Drop:
    checked = check(item)
    assert checked.reports == () and checked.unusable is False
    (drop,) = checked.drops
    return drop


def without(field) -> dict:
    item = proposal()
    del item[field]
    return item


@pytest.mark.parametrize(
    "item",
    [
        "AAA",
        5,
        None,
        ["AAA"],
        without("symbol"),
        without("direction"),
        without("conviction"),
        without("suggested_size_pct"),
        without("rationale"),
        proposal(sources=[]),  # an extra field
        proposal(article_ids=["A1"]),
        proposal(symbol=5),
        proposal(symbol=None),
        proposal(rationale=5),
        proposal(rationale=None),
        proposal(rationale=""),
        proposal(rationale="   \n\t "),
        proposal(rationale="\x00\x07 \ud800"),  # nothing left once cleaned
    ],
)
def test_a_malformed_item_is_malformed_answer(item):
    assert only_drop(item).reason == "malformed_answer"


@pytest.mark.parametrize(
    "symbol", ["CCC", "msft", "MSFT ", " MSFT", "Msft", "MSFT\n", "", "AAA,BBB", "ZZZZZZ"]
)
def test_a_symbol_not_exactly_on_the_shortlist_is_not_shortlisted(symbol):
    drop = only_drop(proposal(symbol=symbol))
    assert drop.reason == "not_shortlisted"


def test_a_listed_and_eligible_symbol_that_was_not_shortlisted_is_dropped():
    # CCC passed the screen but ranked below the cut: only the shortlist counts.
    assert only_drop(proposal(symbol="CCC")).reason == "not_shortlisted"


@pytest.mark.parametrize("direction", ["sell", "hold", "BUY", "Buy", " buy", "", None, 1, ["buy"]])
def test_only_buy_is_a_direction(direction):
    assert only_drop(proposal(direction=direction)).reason == "invalid_direction"


@pytest.mark.parametrize("conviction", [0, 6, -1, 3.5, 3.0, "3", True, False, None, [3], 10**30])
def test_conviction_is_an_integer_from_one_to_five(conviction):
    assert only_drop(proposal(conviction=conviction)).reason == "invalid_conviction"


@pytest.mark.parametrize("conviction", [1, 2, 3, 4, 5])
def test_every_conviction_in_range_passes(conviction):
    assert check(proposal(conviction=conviction)).drops == ()


@pytest.mark.parametrize(
    "size",
    [0, 0.0, 0.0004, 0.0009999, -1, -0.001, 100.01, 101, 1e9, "5", None, True, False, [5], {}],
)
def test_a_size_out_of_range_or_not_a_number_is_invalid_size(size):
    assert only_drop(proposal(suggested_size_pct=size)).reason == "invalid_size"


@pytest.mark.parametrize("size", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_sizes_are_invalid(size):
    # json.loads accepts these bare words, so a model can send them.
    text = (
        '{"proposals": [{"symbol": "AAA", "direction": "buy", "conviction": 3, '
        f'"suggested_size_pct": {size}, "rationale": "x"}}]}}'
    )
    assert check(text=text).drops[0].reason == "invalid_size"


@pytest.mark.parametrize("size", [0.001, 0.0019, 5, 99.9999, 100, 100.0])
def test_a_size_that_rounds_down_above_zero_up_to_100_passes(size):
    assert check(proposal(suggested_size_pct=size)).drops == ()


def test_the_second_proposal_for_a_symbol_is_a_duplicate():
    checked = check(proposal(), proposal(conviction=5), proposal(symbol="BBB"))
    assert [(d.index, d.reason) for d in checked.drops] == [(1, "duplicate_symbol")]
    assert [r.symbol for r in checked.reports] == ["AAA", "BBB"]
    assert checked.reports[0].conviction == 3  # the first one stands


def test_an_invalid_second_proposal_gets_its_own_reason_not_duplicate():
    checked = check(proposal(), proposal(conviction=9))
    assert [d.reason for d in checked.drops] == ["invalid_conviction"]


def test_a_symbol_with_an_open_report_is_already_open():
    checked = check(proposal(), proposal(symbol="BBB"), open_symbols={"BBB"})
    assert [(d.index, d.symbol, d.reason) for d in checked.drops] == [(1, "BBB", "already_open")]
    assert [r.symbol for r in checked.reports] == ["AAA"]


def test_an_open_symbol_off_the_shortlist_is_not_shortlisted_first():
    drop = check(proposal(symbol="ZZZ"), open_symbols={"ZZZ"}).drops[0]
    assert drop.reason == "not_shortlisted"


def test_a_duplicate_of_an_open_symbol_is_already_open_each_time():
    checked = check(proposal(), proposal(), open_symbols={"AAA"})
    assert [d.reason for d in checked.drops] == ["already_open", "already_open"]


def test_the_reasons_are_the_contracts_closed_set():
    assert a.DROP_REASONS == (
        "malformed_answer",
        "not_shortlisted",
        "invalid_direction",
        "invalid_conviction",
        "invalid_size",
        "duplicate_symbol",
        "already_open",
    )


# --- the symbol a drop carries ---------------------------------------------------------------


def test_a_drop_names_a_string_symbol_cleaned_and_cut_to_16_characters():
    drop = only_drop(proposal(symbol="X\x00\n" + "Y" * 100, direction="sell"))
    assert drop.symbol == "X\n" + "Y" * 14 and len(drop.symbol) == 16
    # The reason is the first rule it fails: it isn't on the shortlist.
    assert drop.reason == "not_shortlisted"


def test_a_malformed_item_with_a_string_symbol_still_names_it():
    drop = only_drop(proposal(symbol="AAA", rationale=5))
    assert (drop.symbol, drop.reason) == ("AAA", "malformed_answer")


@pytest.mark.parametrize("item", ["AAA", 5, None, proposal(symbol=5), proposal(symbol=None)])
def test_a_symbol_that_is_not_a_string_is_none(item):
    assert only_drop(item).symbol is None


def test_a_symbol_is_never_longer_than_16_characters_or_unclean():
    for symbol in ["A" * 1000, "\x1b[31mred", "ok\ud800" + "z" * 40]:
        drop = only_drop(proposal(symbol=symbol))
        assert len(drop.symbol) <= 16 and not a.text.has_unsafe(drop.symbol)


# --- the whole answer -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        "null",
        "5",
        '"proposals"',
        "{}",
        '{"proposals": null}',
        '{"proposals": {}}',
        '{"proposals": "x"}',
        '{"proposals": [], "extra": 1}',
        '{"proposal": []}',
        '{"proposals": [}',
    ],
)
def test_a_non_json_answer_or_the_wrong_top_level_shape_is_unusable(text):
    checked = check(text=text)
    assert (checked.unusable, checked.reports, checked.drops, checked.received) == (
        True,
        (),
        (),
        0,
    )


def test_an_empty_proposal_list_is_usable_and_empty():
    checked = check()
    assert (checked.unusable, checked.received, checked.reports, checked.drops) == (
        False,
        0,
        (),
        (),
    )


def test_each_drop_carries_its_index_in_the_answer():
    checked = check(proposal(), "junk", proposal(symbol="ZZZ"), proposal(symbol="BBB"))
    assert [(d.index, d.reason) for d in checked.drops] == [
        (1, "malformed_answer"),
        (2, "not_shortlisted"),
    ]
    assert checked.received == 4
