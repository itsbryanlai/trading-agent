"""service.run, User Story 1: decisions from unexpired reports and fresh state
(specs/008-portfolio-manager spec US1; research P1, P3, P10)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.fakes.market_data import FakeMarketData
from tests.fakes.pm_store import FakePmStore
from tests.unit.portfolio_manager.builders import inputs, journal_row, position, report
from tests.unit.portfolio_manager.service_support import (
    Clock,
    Settings,
    answer_of,
    decide,
    market,
    model_input,
    run,
)
from tests.unit.portfolio_manager.support import NOW


def quote_order(fake):
    return [symbol for _, call, symbol in fake.calls if call == "get_quote"]


def test_a_buy_report_and_a_buying_model_write_one_linked_decision():
    data = inputs([report("db-aapl", "AAPL", "buy", size="5")])
    quotes = market(("AAPL", "201.5"))
    outcome, store, model, _ = run(data, quotes, answer_of(decide("AAPL", "buy", 4.5, ["R1"])))
    assert outcome.failure is None
    (d,) = store.written
    assert (d.symbol, d.direction, d.size_pct) == ("AAPL", "buy", Decimal("4.500"))
    assert d.report_ids == ("db-aapl",)
    assert d.quote == Decimal("201.5")
    assert d.quote_time == NOW - timedelta(minutes=1)
    assert outcome.decisions == (d,)
    assert len(model.calls) == 1
    assert len(store.writes) == 1  # one write for the run


def test_a_sell_report_at_zero_and_a_selling_model_write_a_full_exit():
    data = inputs([report("db-msft", "MSFT", "sell", size="0")], [position("MSFT", "10")])
    outcome, store, model, _ = run(
        data, market(("MSFT", "300")), answer_of(decide("MSFT", "sell", 0, ["R1"]))
    )
    (d,) = store.written
    assert (d.direction, d.size_pct, d.report_ids) == ("sell", Decimal("0.000"), ("db-msft",))
    (r,) = model_input(model)["symbols"][0]["reports"]
    assert r["suggested_size_meaning"] == "full exit"
    assert r["suggested_size_pct"] == "0"


def test_no_unexpired_report_means_no_quote_no_model_call_no_write_and_success():
    for reports in (
        [],
        [report("old", "AAPL", expires_at=NOW), report("q", None, "no_action", size=None)],
    ):
        quotes = market(("AAPL", "200"))
        outcome, store, model, _ = run(inputs(reports, [position("AAPL")]), quotes)
        assert outcome.failure is None
        assert (model.calls, store.writes, quotes.calls) == ([], [], [])
        assert outcome.note == "no unexpired report"


def test_an_expired_report_is_never_given_to_the_model():
    data = inputs(
        [
            report("live", "AAPL", rationale="live thesis"),
            report("old", "MSFT", expires_at=NOW - timedelta(seconds=1), rationale="stale thesis"),
        ]
    )
    quotes = market(("AAPL", "200"), ("MSFT", "300"))
    _, _, model, _ = run(data, quotes)
    text = model.calls[0]["user"]
    assert "live thesis" in text
    assert "stale thesis" not in text
    assert quote_order(quotes) == ["AAPL"]


def test_a_held_symbol_with_no_report_gets_no_decision():
    data = inputs([report("db-aapl", "AAPL")], [position("TSLA", "10")])
    quotes = market(("AAPL", "200"), ("TSLA", "250"))
    outcome, store, model, _ = run(
        data, quotes, answer_of(decide("TSLA", "sell", 0, ["R1"]), decide("AAPL", "buy", 4))
    )
    assert [d.symbol for d in store.written] == ["AAPL"]
    assert [(x.symbol, x.reason) for x in outcome.drops] == [("TSLA", "unknown_symbol")]
    doc = model_input(model)
    assert [s["symbol"] for s in doc["symbols"]] == ["AAPL"]
    assert [p["symbol"] for p in doc["positions"]] == ["TSLA"]
    assert doc["positions"][0]["quote"] == "250"  # held positions are shown with a quote


def test_quotes_are_fetched_for_candidates_newest_first_then_held_symbols():
    data = inputs(
        [
            report("a", "AAPL", generated_at=NOW - timedelta(hours=3)),
            report("m", "MSFT", generated_at=NOW - timedelta(minutes=5)),
        ],
        [position("TSLA", "1"), position("AAPL", "1")],
    )
    quotes = market(("AAPL", "1"), ("MSFT", "1"), ("TSLA", "1"))
    run(data, quotes)
    assert quote_order(quotes) == ["MSFT", "AAPL", "TSLA"]


def test_calls_are_paced_and_never_before_the_first():
    data = inputs([report("a", "AAPL"), report("m", "MSFT")], [position("TSLA", "1")])
    _, _, _, clock = run(data, market(("AAPL", "1"), ("MSFT", "1"), ("TSLA", "1")))
    assert clock.sleeps == [2.0, 2.0]  # 30 calls a minute


def test_each_quote_is_judged_at_its_own_fetch_time():
    """analyze F1: trade 80 s after the run started, fetched 90 s in: fresh."""

    class SlowQuotes(FakeMarketData):
        def get_quote(self, symbol):
            clock.advance(90)
            return super().get_quote(symbol)

    clock = Clock()
    quotes = SlowQuotes()
    quotes.add("AAPL", current="200", quote_time=NOW + timedelta(seconds=80))
    data = inputs([report("db-aapl", "AAPL")])
    outcome, store, _, _ = run(data, quotes, answer_of(decide()), clock=clock)
    assert outcome.skipped == ()
    assert [d.symbol for d in store.written] == ["AAPL"]


def test_a_quote_the_phase_deadline_prevented_is_missing():
    data = inputs(
        [
            report("a", "AAPL", generated_at=NOW),
            report("m", "MSFT", generated_at=NOW - timedelta(hours=1)),
        ]
    )

    class SlowQuotes(FakeMarketData):
        def get_quote(self, symbol):
            clock.advance(100)  # past quote_phase_seconds after the first call
            return super().get_quote(symbol)

    clock = Clock()
    quotes = SlowQuotes()
    quotes.add("AAPL", current="200", quote_time=NOW + timedelta(seconds=99))
    quotes.add("MSFT", current="300", quote_time=NOW)
    outcome, _, model, _ = run(data, quotes, answer_of(), clock=clock)
    assert quote_order(quotes) == ["AAPL"]
    assert outcome.skipped == (("MSFT", "quote_missing"),)
    assert [s["symbol"] for s in model_input(model)["symbols"]] == ["AAPL"]


def test_a_provider_error_on_one_symbol_makes_only_that_quote_missing():
    from trading_agent.reference.provider import ProviderUnavailable

    quotes = market(("AAPL", "200"), ("MSFT", "300"))
    quotes.fail("get_quote", "MSFT", error=ProviderUnavailable())
    data = inputs([report("a", "AAPL"), report("m", "MSFT")])
    outcome, _, model, _ = run(data, quotes)
    assert outcome.skipped == (("MSFT", "quote_missing"),)
    assert [s["symbol"] for s in model_input(model)["symbols"]] == ["AAPL"]


def test_a_stale_quote_skips_its_symbol_and_the_others_are_decided():
    data = inputs([report("a", "AAPL"), report("m", "MSFT")])
    quotes = FakeMarketData()
    quotes.add("AAPL", current="200", quote_time=NOW - timedelta(minutes=1))
    quotes.add("MSFT", current="300", quote_time=NOW - timedelta(minutes=20))
    outcome, store, _, _ = run(data, quotes, answer_of(decide("AAPL")))
    assert outcome.skipped == (("MSFT", "quote_stale"),)
    assert [d.symbol for d in store.written] == ["AAPL"]


def test_the_state_is_read_once_with_the_run_start_and_journal_length():
    data = inputs([report("a", "AAPL")], journal=[journal_row()])
    _, store, model, _ = run(data, market(("AAPL", "1")), settings=Settings(journal_entries=3))
    assert store.reads == [(NOW, 3)]
    assert model_input(model)["journal"][0]["summary"] == "A quiet day."


def test_a_hold_is_written_with_the_current_weight():
    data = inputs([report("db-m", "MSFT", "sell", size="2")], [position("MSFT", "50")])
    _, store, _, _ = run(
        data, market(("MSFT", "300")), answer_of(decide("MSFT", "hold", 99, ["R1"]))
    )
    (d,) = store.written
    assert (d.direction, d.size_pct) == ("hold", Decimal("15.000"))


def test_a_run_where_every_proposal_is_dropped_writes_nothing_and_succeeds():
    data = inputs([report("a", "AAPL")])
    outcome, store, _, _ = run(data, market(("AAPL", "1")), answer_of(decide("XYZ")))
    assert outcome.failure is None
    assert store.writes == []
    assert outcome.note == "no decision survived"


def test_an_empty_answer_writes_nothing_and_succeeds():
    outcome, store, _, _ = run(inputs([report("a", "AAPL")]), market(("AAPL", "1")), answer_of())
    assert (outcome.failure, store.writes, outcome.note) == (None, [], "the model proposed none")


def test_the_market_closed_at_the_start_does_nothing_at_all():
    clock = Clock(datetime(2026, 10, 3, 15, 0, tzinfo=UTC))  # a Saturday
    quotes = market(("AAPL", "1"))
    outcome, store, model, _ = run(inputs([report("a", "AAPL")]), quotes, clock=clock)
    assert outcome.market_closed
    assert (store.reads, store.writes, model.calls, quotes.calls) == ([], [], [], [])


def test_the_market_closing_during_the_run_writes_nothing():
    clock = Clock()

    class SlowModel:
        def complete(self, system, user, schema):
            clock.now = datetime(2026, 10, 1, 20, 30, tzinfo=UTC)  # after the close
            from trading_agent.llm.ports import ModelReply

            return ModelReply(
                '{"decisions": [{"symbol": "AAPL", "direction": "buy", "target_weight_pct": 4,'
                ' "reasoning": "r", "report_ids": ["R1"]}]}',
                1,
                1,
                "stop",
            )

    outcome, store, _, _ = run(
        inputs([report("a", "AAPL")]), market(("AAPL", "1")), clock=clock, model=SlowModel()
    )
    assert outcome.failure == "window_closed"
    assert (store.writes, outcome.decisions) == ([], ())


def test_no_account_snapshot_ends_the_run_before_any_quote_or_model_call():
    quotes = market(("AAPL", "1"))
    outcome, store, model, _ = run(inputs([report("a", "AAPL")], account=False), quotes)
    assert outcome.failure == "no_account_snapshot"
    assert (model.calls, store.writes, quotes.calls) == ([], [], [])
    zero = inputs([report("a", "AAPL")], equity="0")
    assert run(zero, quotes)[0].failure == "no_account_snapshot"


def test_an_answer_that_is_not_decisions_fails_as_unusable_and_writes_nothing():
    outcome, store, _, _ = run(inputs([report("a", "AAPL")]), market(("AAPL", "1")), "not json")
    assert outcome.failure == "unusable_answer"
    assert store.writes == []


def test_a_store_that_fails_to_write_raises_for_main_to_map(caplog):
    import pytest

    from trading_agent.portfolio_manager.store import StoreError

    data = inputs([report("a", "AAPL")])
    with pytest.raises(StoreError):
        run(
            data,
            market(("AAPL", "1")),
            answer_of(decide()),
            store=FakePmStore(data, fail_write=True),
        )


def test_logs_follow_research_p14_and_never_carry_text_from_the_model(caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.portfolio_manager")
    data = inputs([report("a", "AAPL", rationale="SENTINEL-RATIONALE")])
    run(data, market(("AAPL", "1")), answer_of(decide(reasoning="SENTINEL-REASONING")))
    text = caplog.text
    assert "run started (prompt v0.1, provider qwen, model qwen3.7-plus)" in text
    assert "1 reports on 1 symbols (0 already decided on); 0 held" in text
    assert "1 fresh quotes; skipped: none" in text
    assert "model used 1200 input and 300 output tokens" in text
    assert "1 decisions received, 1 accepted, 0 dropped" in text
    assert "wrote 1 decision(s)" in text
    assert "SENTINEL" not in text


def test_when_no_candidate_has_a_fresh_quote_the_model_is_not_called():
    quotes = FakeMarketData()
    quotes.add("AAPL", current="200", quote_time=NOW - timedelta(hours=1))
    outcome, store, model, _ = run(inputs([report("a", "AAPL")]), quotes)
    assert (model.calls, store.writes) == ([], [])
    assert outcome.skipped == (("AAPL", "quote_stale"),)
    assert outcome.failure == "no_fresh_quotes"  # the cases are in test_service_failures.py


def test_the_model_gets_the_fixed_prompt_the_schema_and_the_document():
    from trading_agent.portfolio_manager import prompt
    from trading_agent.portfolio_manager.answer import ANSWER_SCHEMA

    _, _, model, _ = run(inputs([report("a", "AAPL")]), market(("AAPL", "200")))
    (call,) = model.calls
    assert call["system"] == prompt.SYSTEM_PROMPT
    assert call["schema"] == ANSWER_SCHEMA
    assert call["user"].startswith('{"now": "2026-10-01T14:00:00+00:00"')


def test_the_reasoning_limit_comes_from_the_config():
    outcome, store, _, _ = run(
        inputs([report("a", "AAPL")]),
        market(("AAPL", "200")),
        answer_of(decide(reasoning="x" * 50)),
        settings=Settings(reasoning_max_chars=10),
    )
    assert store.written[0].reasoning == "x" * 9 + "…"


def test_the_outcome_reports_what_the_run_saw():
    data = inputs([report("a", "AAPL")])
    outcome, _, model, _ = run(data, market(("AAPL", "200")), answer_of(decide()))
    assert outcome.candidates == ["AAPL"]
    assert outcome.input_chars == len(model.calls[0]["user"])
    assert (outcome.input_tokens, outcome.output_tokens) == (1200, 300)


def test_the_inputs_line_counts_consumed_reports_and_held_positions(caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.portfolio_manager")
    data = inputs(
        [report("a", "AAPL", consumed=True), report("b", "AAPL", agent="opportunistic_identifier")],
        [position("TSLA", "1"), position("IBM", "1")],
    )
    run(data, market(("AAPL", "200"), ("TSLA", "1"), ("IBM", "1")))
    assert "2 reports on 1 symbols (1 already decided on); 2 held" in caplog.text


def test_a_provider_errors_text_never_reaches_the_log(caplog):
    from trading_agent.reference.provider import ProviderUnavailable

    caplog.set_level(logging.INFO, logger="trading_agent.portfolio_manager")
    quotes = market(("AAPL", "200"), ("MSFT", "300"))
    quotes.fail("get_quote", "MSFT", error=ProviderUnavailable("SENTINEL-BODY"))
    run(inputs([report("a", "AAPL"), report("m", "MSFT")]), quotes)
    assert "quote for MSFT missing: ProviderUnavailable" in caplog.text
    assert "SENTINEL-BODY" not in caplog.text
