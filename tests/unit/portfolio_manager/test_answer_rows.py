"""answer.check, the happy path (specs/008-portfolio-manager research P8; pm-interface.md).

The drop rules are in test_answer_drops.py and test_answer_property.py."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

from tests.unit.portfolio_manager.builders import fetched, inputs, position, report
from tests.unit.portfolio_manager.support import MAX_AGE, NOW
from trading_agent.portfolio_manager import answer
from trading_agent.portfolio_manager.inputs import build_candidates

CAP = 40


def make_given(cap=CAP):
    data = inputs(
        [
            report("db-aapl", "AAPL", "buy", size="5"),
            report("db-aapl-2", "AAPL", "buy", agent="opportunistic_identifier", size="4"),
            report("db-msft", "MSFT", "sell", size="0"),
            report("db-nvda", "NVDA", "buy", size="3"),
        ],
        [position("MSFT", "50"), position("NVDA", "33.333")],
        equity="100000",
    )
    quotes = {s: fetched(s, p) for s, p in (("AAPL", "200"), ("MSFT", "300"), ("NVDA", "100"))}
    built = build_candidates(data, quotes, run_start=NOW, max_age=MAX_AGE)
    return built.given(reasoning_max_chars=cap)


GIVEN = make_given()
# R1 AAPL (db-aapl), R2 AAPL (db-aapl-2), R3 MSFT (db-msft), R4 NVDA (db-nvda)


def item(symbol="AAPL", direction="buy", target=4, reasoning="Because.", ids=("R1",)):
    return {
        "symbol": symbol,
        "direction": direction,
        "target_weight_pct": target,
        "reasoning": reasoning,
        "report_ids": list(ids),
    }


def run(*items, given=GIVEN):
    return answer.check(json.dumps({"decisions": list(items)}), given)


def test_a_valid_buy_becomes_a_decision_with_the_runs_quote_and_time():
    checked = run(item("AAPL", "buy", 4.5, "Earnings beat.", ("R1",)))
    assert checked.drops == ()
    (d,) = checked.decisions
    assert (d.symbol, d.direction, d.size_pct) == ("AAPL", "buy", Decimal("4.500"))
    assert d.reasoning == "Earnings beat."
    assert d.quote == Decimal(200)
    assert d.quote_time == GIVEN.symbols["AAPL"].quote_time
    assert d.report_ids == ("db-aapl",)


def test_a_valid_sell_to_zero_is_a_full_exit():
    (d,) = run(item("MSFT", "sell", 0, "Exit.", ("R3",))).decisions
    assert (d.symbol, d.direction, d.size_pct) == ("MSFT", "sell", Decimal("0.000"))
    assert d.report_ids == ("db-msft",)


def test_sizes_are_rounded_down_to_three_places():
    (d,) = run(item("AAPL", "buy", 4.99999)).decisions
    assert d.size_pct == Decimal("4.999")
    (d,) = run(item("AAPL", "buy", 100)).decisions
    assert d.size_pct == Decimal("100.000")


def test_reasoning_is_trimmed_and_cut_with_an_ellipsis():
    (d,) = run(item(reasoning="   short   ")).decisions
    assert d.reasoning == "short"
    (d,) = run(item(reasoning="x" * 500)).decisions
    assert len(d.reasoning) == CAP
    assert d.reasoning == "x" * (CAP - 1) + "…"
    (d,) = run(item(reasoning="y" * CAP)).decisions
    assert d.reasoning == "y" * CAP  # exactly at the cap: untouched


def test_reasoning_loses_characters_postgres_refuses():
    (d,) = run(item(reasoning="a\x00b\ud800c\nd")).decisions
    assert d.reasoning == "abc\nd"


def test_report_ids_are_deduplicated_in_order():
    (d,) = run(item("AAPL", "buy", 4, ids=("R2", "R1", "R2"))).decisions
    assert d.report_ids == ("db-aapl-2", "db-aapl")


def test_a_hold_gets_the_current_weight_whatever_the_model_said():
    # MSFT: 50 x 300 / 100000 = 15%
    (d,) = run(item("MSFT", "hold", 99, "Wait.", ("R3",))).decisions
    assert (d.direction, d.size_pct) == ("hold", Decimal("15.000"))


def test_a_hold_weight_is_rounded_down_and_capped_at_100():
    (d,) = run(item("NVDA", "hold", 0, ids=("R4",))).decisions
    assert d.size_pct == Decimal("3.333")  # 33.333 x 100 / 100000 = 3.3333
    heavy = make_given()
    heavy_facts = dict(heavy.symbols)
    heavy_facts["AAPL"] = replace(heavy_facts["AAPL"], current_weight_pct=Decimal("130.5"))
    capped = replace(heavy, symbols=heavy_facts)
    (d,) = run(item("AAPL", "hold", 1), given=capped).decisions
    assert d.size_pct == Decimal("100.000")


def test_an_unheld_hold_has_weight_zero():
    (d,) = run(item("AAPL", "hold", 50)).decisions
    assert d.size_pct == Decimal("0.000")


def test_an_empty_decisions_list_is_a_normal_answer():
    checked = run()
    assert (checked.decisions, checked.drops, checked.unusable, checked.received) == (
        (),
        (),
        False,
        0,
    )


def test_several_valid_items_keep_the_models_order():
    checked = run(item("NVDA", "buy", 4, ids=("R4",)), item("AAPL", "buy", 4))
    assert [d.symbol for d in checked.decisions] == ["NVDA", "AAPL"]
    assert checked.received == 2


def test_a_symbol_not_given_is_dropped_as_unknown_symbol():
    checked = run(item("XYZ", "buy", 4, ids=("R1",)), item("AAPL", "buy", 4))
    assert [d.symbol for d in checked.decisions] == ["AAPL"]
    assert [(d.index, d.symbol, d.reason) for d in checked.drops] == [(0, "XYZ", "unknown_symbol")]


def test_a_symbol_whose_quote_was_unusable_is_unknown_to_the_run():
    data = inputs([report("db-1", "AAPL"), report("db-2", "MSFT")])
    built = build_candidates(
        data, {"AAPL": fetched("AAPL")}, run_start=NOW, max_age=MAX_AGE
    )  # MSFT: no quote
    checked = run(item("MSFT", "buy", 4, ids=("R1",)), given=built.given(reasoning_max_chars=CAP))
    assert [d.reason for d in checked.drops] == ["unknown_symbol"]


def test_a_malformed_item_is_dropped_and_the_rest_survive():
    bad = [
        "AAPL",
        None,
        {"symbol": "AAPL"},
        {**item(), "extra": 1},
        {k: v for k, v in item().items() if k != "reasoning"},
        item(symbol=5),
        item(direction=None),
        item(target="4"),
        item(target=True),
        item(reasoning=7),
        {**item(), "report_ids": "R1"},
        item(ids=[1]),
    ]
    checked = run(*bad, item("AAPL", "buy", 4))
    assert len(checked.decisions) == 1
    assert [d.reason for d in checked.drops] == ["malformed_decision"] * len(bad)
    assert [d.index for d in checked.drops] == list(range(len(bad)))
    assert checked.received == len(bad) + 1


def test_a_whole_answer_not_shaped_like_decisions_is_unusable():
    for text in (
        "not json",
        "[]",
        "null",
        "{}",
        '{"decisions": {}}',
        '{"decisions": [], "extra": 1}',
        '{"proposals": []}',
    ):
        checked = answer.check(text, GIVEN)
        assert checked.unusable, text
        assert (checked.decisions, checked.drops) == ((), ())


def test_the_schema_matches_the_checkers_fields():
    props = answer.ANSWER_SCHEMA["properties"]["decisions"]["items"]
    assert set(props["properties"]) == {
        "symbol",
        "direction",
        "target_weight_pct",
        "reasoning",
        "report_ids",
    }
    assert props["required"] == list(props["properties"])
    assert props["additionalProperties"] is False
    assert props["properties"]["direction"]["enum"] == ["buy", "sell", "hold"]
    assert answer.ANSWER_SCHEMA["required"] == ["decisions"]
    assert answer.ANSWER_SCHEMA["additionalProperties"] is False


def test_the_shape_rule_alone_rejects_wrong_types():
    """The shape check is its own rule; later rules rely on it."""
    wrong_ids = {**item(), "report_ids": "R1"}
    for bad in (item(target=True), item(target="4"), item(ids=[1]), wrong_ids):
        assert answer._malformed(bad, GIVEN, set()) == "malformed_decision"
    assert answer._malformed(item(), GIVEN, set()) is None
