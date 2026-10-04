"""inputs.build_candidates and identifiers (specs/008-portfolio-manager research P6, P7)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tests.unit.portfolio_manager.builders import (
    earlier_decision,
    fetched,
    inputs,
    position,
    report,
)
from tests.unit.portfolio_manager.support import MAX_AGE, NOW
from trading_agent.portfolio_manager.inputs import build_candidates, symbols_to_quote


def build(data, quotes=None, **kw):
    quotes = quotes if quotes is not None else {}
    return build_candidates(data, quotes, run_start=NOW, max_age=MAX_AGE, **kw)


def all_quotes(*symbols):
    return {s: fetched(s) for s in symbols}


def test_expired_and_no_action_reports_are_excluded():
    data = inputs(
        [
            report("live", "AAPL"),
            report("old", "MSFT", expires_at=NOW),  # expires_at <= run_start
            report("older", "NVDA", expires_at=NOW - timedelta(minutes=1)),
            report("quiet", None, "no_action", size=None),
            report("odd", "TSLA", "no_action", size=None),
        ]
    )
    built = build(data, all_quotes("AAPL", "MSFT", "NVDA", "TSLA"))
    assert [c.symbol for c in built.candidates] == ["AAPL"]
    assert symbols_to_quote(data, NOW) == ["AAPL"]


def test_a_consumed_report_is_kept_and_marked():
    data = inputs(
        [report("a", "AAPL", consumed=True), report("b", "AAPL", agent="opportunistic_identifier")]
    )
    candidate = build(data, all_quotes("AAPL")).candidates[0]
    assert {r.agent: r.already_decided_on for r in candidate.reports} == {
        "research": True,
        "opportunistic_identifier": False,
    }


def test_candidates_are_ordered_newest_report_first():
    data = inputs(
        [
            report("a", "AAPL", generated_at=NOW - timedelta(hours=3)),
            report("m", "MSFT", generated_at=NOW - timedelta(minutes=10)),
            report("n", "NVDA", generated_at=NOW - timedelta(hours=1)),
            report("m2", "MSFT", generated_at=NOW - timedelta(hours=4)),
        ]
    )
    built = build(data, all_quotes("AAPL", "MSFT", "NVDA"))
    assert [c.symbol for c in built.candidates] == ["MSFT", "NVDA", "AAPL"]


def test_report_ids_follow_symbol_then_time_then_id():
    data = inputs(
        [
            report("z-id", "MSFT", generated_at=NOW - timedelta(hours=3)),
            report("b-id", "AAPL", generated_at=NOW - timedelta(hours=1)),
            report("a-id", "AAPL", generated_at=NOW - timedelta(hours=1)),
            report("early", "AAPL", generated_at=NOW - timedelta(hours=2)),
        ]
    )
    built = build(data, all_quotes("AAPL", "MSFT"))
    assert {run_id: ref.report_id for run_id, ref in built.refs.items()} == {
        "R1": "early",
        "R2": "a-id",
        "R3": "b-id",
        "R4": "z-id",
    }
    assert built.refs["R4"].symbol == "MSFT"
    assert built.refs["R1"].direction == "buy"
    aapl = next(c for c in built.candidates if c.symbol == "AAPL")
    assert [r.run_id for r in aapl.reports] == ["R1", "R2", "R3"]


def test_a_symbol_without_a_fresh_quote_is_skipped_with_its_reason():
    data = inputs([report("a", "AAPL"), report("m", "MSFT"), report("n", "NVDA")])
    quotes = {
        "AAPL": fetched("AAPL"),
        "MSFT": fetched("MSFT", at=NOW - timedelta(minutes=30)),  # stale
        # NVDA never fetched: missing
    }
    built = build(data, quotes)
    assert [c.symbol for c in built.candidates] == ["AAPL"]
    assert dict(built.skipped) == {"MSFT": "quote_stale", "NVDA": "quote_missing"}
    assert set(built.refs) == {"R1"}  # skipped symbols' reports are never given to the model


def test_a_fetch_that_returned_nothing_counts_as_missing():
    from trading_agent.portfolio_manager.inputs import FetchedQuote

    built = build(inputs([report("a", "AAPL")]), {"AAPL": FetchedQuote(None, NOW)})
    assert built.skipped == (("AAPL", "quote_missing"),)


def test_each_quote_is_judged_at_its_own_fetch_time():
    data = inputs([report("a", "AAPL")])
    trade = NOW + timedelta(seconds=80)
    quote_fetched_90s_in = fetched("AAPL", at=trade, fetched_at=NOW + timedelta(seconds=90))
    assert [c.symbol for c in build(data, {"AAPL": quote_fetched_90s_in}).candidates] == ["AAPL"]


def test_a_held_symbol_with_no_report_is_a_position_but_never_a_candidate():
    data = inputs([report("a", "AAPL")], [position("TSLA", "10"), position("AAPL", "5")])
    built = build(data, all_quotes("AAPL", "TSLA"))
    assert [c.symbol for c in built.candidates] == ["AAPL"]
    assert {p.symbol for p in built.positions} == {"TSLA", "AAPL"}
    assert symbols_to_quote(data, NOW) == ["AAPL", "TSLA"]  # candidates first, then held


def test_current_weight_is_value_over_equity_in_decimal():
    data = inputs([report("a", "AAPL")], [position("AAPL", "100")], equity="100000")
    candidate = build(data, {"AAPL": fetched("AAPL", "200.50")}).candidates[0]
    assert (
        candidate.current_weight_pct == Decimal("100") * Decimal("200.50") / Decimal(100000) * 100
    )
    assert candidate.quote == Decimal("200.50")


def test_an_unheld_symbol_has_weight_zero():
    candidate = build(inputs([report("a", "AAPL")]), all_quotes("AAPL")).candidates[0]
    assert candidate.current_weight_pct == 0


def test_positions_carry_quote_and_weight_or_none_without_a_fresh_quote():
    data = inputs([], [position("TSLA", "10"), position("IBM", "10")], equity="10000")
    built = build(
        data, {"TSLA": fetched("TSLA", "100"), "IBM": fetched("IBM", at=NOW - timedelta(hours=1))}
    )
    by_symbol = {p.symbol: p for p in built.positions}
    assert (by_symbol["TSLA"].quote, by_symbol["TSLA"].weight_pct) == (Decimal(100), Decimal(10))
    assert (by_symbol["IBM"].quote, by_symbol["IBM"].weight_pct) == (None, None)


def test_suggested_size_meaning():
    data = inputs(
        [
            report("exit", "AAPL", "sell", size="0"),
            report("trim", "MSFT", "sell", size="2"),
            report("buy", "NVDA", "buy", size="0.5"),
            report("odd", "TSLA", "buy", size="0"),
        ]
    )
    built = build(data, all_quotes("AAPL", "MSFT", "NVDA", "TSLA"))
    meaning = {r.symbol: r.size_meaning for c in built.candidates for r in c.reports}
    assert meaning == {
        "AAPL": "full exit",
        "MSFT": "target weight",
        "NVDA": "target weight",
        "TSLA": "target weight",
    }


def test_earlier_decisions_today_are_attached_per_symbol_without_reasoning():
    data = inputs(
        [report("a", "AAPL"), report("m", "MSFT")],
        earlier=[earlier_decision("AAPL", "buy", "3"), earlier_decision("TSLA", "sell", "0")],
    )
    built = build(data, all_quotes("AAPL", "MSFT"))
    by_symbol = {c.symbol: c.earlier_decisions for c in built.candidates}
    assert len(by_symbol["AAPL"]) == 1
    assert (by_symbol["AAPL"][0].direction, by_symbol["AAPL"][0].size_pct) == ("buy", Decimal(3))
    assert by_symbol["MSFT"] == ()
    assert not hasattr(by_symbol["AAPL"][0], "reasoning")


def test_the_given_facts_cover_exactly_the_candidates():
    data = inputs([report("a", "AAPL")], [position("AAPL", "100")], equity="100000")
    built = build(data, all_quotes("AAPL"))
    given = built.given(reasoning_max_chars=500)
    assert set(given.symbols) == {"AAPL"}
    assert given.symbols["AAPL"].quote == Decimal(200)
    assert given.symbols["AAPL"].current_weight_pct == Decimal("20")
    assert given.reasoning_max_chars == 500
    assert given.refs is built.refs


def test_no_snapshot_is_refused_here():
    import pytest

    with pytest.raises(ValueError, match="account"):
        build(inputs([report("a", "AAPL")], account=False), all_quotes("AAPL"))
