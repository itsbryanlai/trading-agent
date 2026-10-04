"""service.run, User Story 4: a quiet run and a broken run look different
(specs/008-portfolio-manager spec US4; research P9). Each fake fails in turn; every failure
writes nothing and names its category. A database failure is not a category: it escapes
as StoreError (exit 3)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from tests.fakes.market_data import FakeMarketData
from tests.fakes.model import FakeModel
from tests.fakes.pm_store import FakePmStore
from tests.unit.portfolio_manager.builders import inputs, position, report
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
from trading_agent.llm.ports import (
    ModelError,
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
)
from trading_agent.portfolio_manager.store import StoreError
from trading_agent.reference.provider import KeyRejected, ProviderUnavailable

LOG = "trading_agent.portfolio_manager"


def three_reports():
    return inputs(
        [
            report("a", "AAPL", generated_at=NOW - timedelta(minutes=3)),
            report("m", "MSFT", generated_at=NOW - timedelta(minutes=2)),
            report("n", "NVDA", generated_at=NOW - timedelta(minutes=1)),
        ]
    )


def failed(outcome, store, category):
    assert outcome.failure == category
    assert outcome.decisions == ()
    assert store.writes == []  # a failed run writes nothing


# --- the state ---------------------------------------------------------------------------


@pytest.mark.parametrize("equity", ["0", "-5"])
def test_no_usable_snapshot_fails_before_any_quote_or_model_call(equity):
    quotes = market(("AAPL", "200"))
    outcome, store, model, _ = run(inputs([report("a", "AAPL")], equity=equity), quotes)
    failed(outcome, store, "no_account_snapshot")
    assert (quotes.calls, model.calls) == ([], [])


def test_no_snapshot_today_fails_the_same_way():
    quotes = market(("AAPL", "200"))
    outcome, store, model, _ = run(inputs([report("a", "AAPL")], account=False), quotes)
    failed(outcome, store, "no_account_snapshot")
    assert (quotes.calls, model.calls) == ([], [])


def test_a_quiet_day_is_still_not_a_failure():
    outcome, store, model, _ = run(inputs([]), market())
    assert (outcome.failure, model.calls, store.writes) == (None, [], [])


# --- quotes ------------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["NVDA", "AAPL", "MSFT"])
def test_a_rejected_quote_key_on_any_quote_fails_the_run(symbol, caplog):
    quotes = market(("AAPL", "200"), ("MSFT", "300"), ("NVDA", "100"))
    quotes.fail("get_quote", symbol, error=KeyRejected())
    with caplog.at_level(logging.INFO, logger=LOG):
        outcome, store, model, _ = run(three_reports(), quotes, answer_of(decide("AAPL")))
    failed(outcome, store, "quote_key_rejected")
    assert model.calls == []
    assert "quote_key_rejected: KeyRejected" in caplog.text


def test_one_stale_quote_among_three_leaves_the_other_two_decided(caplog):
    quotes = FakeMarketData()
    quotes.add("AAPL", current="200", quote_time=NOW - timedelta(minutes=1))
    quotes.add("MSFT", current="300", quote_time=NOW - timedelta(minutes=30))
    quotes.add("NVDA", current="100", quote_time=NOW - timedelta(minutes=1))
    answer = answer_of(decide("AAPL", ids=["R1"]), decide("NVDA", ids=["R2"]))
    with caplog.at_level(logging.INFO, logger=LOG):
        outcome, store, _, _ = run(three_reports(), quotes, answer)
    assert outcome.failure is None
    assert sorted(d.symbol for d in store.written) == ["AAPL", "NVDA"]
    assert outcome.skipped == (("MSFT", "quote_stale"),)
    assert "skipped: MSFT (quote_stale)" in caplog.text


@pytest.mark.parametrize("why", ["stale", "missing", "not positive"])
def test_every_candidate_without_a_usable_quote_fails_with_no_fresh_quotes(why, caplog):
    quotes = FakeMarketData()
    for symbol in ("AAPL", "MSFT", "NVDA"):
        if why == "stale":
            quotes.add(symbol, current="100", quote_time=NOW - timedelta(hours=2))
        elif why == "not positive":
            quotes.add(symbol, current="0", quote_time=NOW - timedelta(minutes=1))
        else:
            quotes.add(symbol, current="100")
            quotes.fail("get_quote", symbol, error=ProviderUnavailable())
    with caplog.at_level(logging.INFO, logger=LOG):
        outcome, store, model, _ = run(three_reports(), quotes, answer_of(decide()))
    failed(outcome, store, "no_fresh_quotes")
    assert model.calls == []
    assert len(outcome.skipped) == 3
    assert "no_fresh_quotes" in caplog.text


def test_a_held_symbols_missing_quote_alone_does_not_fail_the_run():
    quotes = market(("AAPL", "200"))
    quotes.add("TSLA", current="250")
    quotes.fail("get_quote", "TSLA", error=ProviderUnavailable())
    data = inputs([report("a", "AAPL")], [position("TSLA", "5")])
    outcome, store, _, _ = run(data, quotes, answer_of(decide()))
    assert outcome.failure is None
    assert [d.symbol for d in store.written] == ["AAPL"]


def test_the_quote_phase_deadline_leaves_the_remaining_symbols_missing():
    class SlowQuotes(FakeMarketData):
        def get_quote(self, symbol):
            clock.advance(100)  # past quote_phase_seconds after the first call
            return super().get_quote(symbol)

    clock = Clock()
    quotes = SlowQuotes()
    for symbol in ("AAPL", "MSFT", "NVDA"):
        quotes.add(symbol, current="100", quote_time=NOW + timedelta(seconds=99))
    outcome, store, model, _ = run(three_reports(), quotes, answer_of(), clock=clock)
    assert outcome.failure is None
    assert [symbol for _, call, symbol in quotes.calls if call == "get_quote"] == ["NVDA"]
    assert outcome.skipped == (("MSFT", "quote_missing"), ("AAPL", "quote_missing"))
    assert [s["symbol"] for s in model_input(model)["symbols"]] == ["NVDA"]


def test_the_deadline_with_the_only_fetch_failed_is_no_fresh_quotes():
    class SlowQuotes(FakeMarketData):
        def get_quote(self, symbol):
            clock.advance(100)
            return super().get_quote(symbol)

    clock = Clock()
    quotes = SlowQuotes()
    quotes.add("NVDA", current="100")
    quotes.fail("get_quote", "NVDA", error=ProviderUnavailable())
    outcome, store, model, _ = run(three_reports(), quotes, clock=clock)
    failed(outcome, store, "no_fresh_quotes")
    assert model.calls == []
    assert {reason for _, reason in outcome.skipped} == {"quote_missing"}


# --- the model ---------------------------------------------------------------------------

MODEL_ERRORS = [
    (ModelKeyRejected("SENTINEL-TEXT", status=401), "model_key_rejected", "HTTP 401"),
    (ModelRejected("SENTINEL-TEXT", status=400), "model_rejected_request", "HTTP 400"),
    (ModelUnavailable("SENTINEL-TEXT", status=503), "model_unavailable", "HTTP 503"),
    (ModelUnavailable("SENTINEL-TEXT"), "model_unavailable", ""),
    (ModelRefused("SENTINEL-TEXT"), "model_refused", ""),
    (ModelTruncated("SENTINEL-TEXT"), "model_truncated", ""),
]


@pytest.mark.parametrize(("error", "category", "status"), MODEL_ERRORS)
def test_each_model_error_has_its_own_category_and_no_second_try(error, category, status, caplog):
    with caplog.at_level(logging.INFO, logger=LOG):
        outcome, store, model, _ = run(
            inputs([report("a", "AAPL")]), market(("AAPL", "200")), model=FakeModel(error=error)
        )
    failed(outcome, store, category)
    assert len(model.calls) == 1  # no retry, no second provider
    assert f"{category}: {type(error).__name__}" in caplog.text
    assert ("(" + status + ")" in caplog.text) == bool(status)
    assert "SENTINEL" not in caplog.text  # a provider's text never reaches a log


@pytest.mark.parametrize(
    "bad", ["not json at all", "[]", '{"proposals": []}', '{"decisions": {}}', "", "null"]
)
def test_an_answer_that_is_not_the_required_shape_fails_as_unusable(bad):
    outcome, store, _, _ = run(inputs([report("a", "AAPL")]), market(("AAPL", "200")), bad)
    failed(outcome, store, "unusable_answer")


def test_an_empty_answer_is_a_success_that_writes_nothing():
    outcome, store, _, _ = run(inputs([report("a", "AAPL")]), market(("AAPL", "200")), answer_of())
    assert (outcome.failure, store.writes) == (None, [])


def test_the_market_closing_during_the_run_writes_nothing_and_fails():
    clock = Clock()

    class SlowModel(FakeModel):
        def complete(self, system, user, schema):
            clock.advance(6 * 3600 + 60)  # 16:01 ET
            return super().complete(system, user, schema)

    model = SlowModel(answer_of(decide()))
    outcome, store, _, _ = run(
        inputs([report("a", "AAPL")]), market(("AAPL", "200")), clock=clock, model=model
    )
    failed(outcome, store, "window_closed")
    assert len(model.calls) == 1


def test_a_run_that_starts_after_the_close_does_nothing_and_succeeds():
    clock = Clock(NOW + timedelta(hours=7))
    quotes = market(("AAPL", "200"))
    outcome, store, model, _ = run(
        inputs([report("a", "AAPL")]), quotes, answer_of(decide()), clock=clock
    )
    assert (outcome.failure, outcome.market_closed) == (None, True)
    assert (store.reads, quotes.calls, model.calls) == ([], [], [])


# --- anything unexpected -----------------------------------------------------------------


def test_an_unexpected_error_in_the_quote_phase_is_an_internal_error(caplog):
    class Broken(FakeMarketData):
        def get_quote(self, symbol):
            raise RuntimeError("SENTINEL-TEXT")

    with caplog.at_level(logging.INFO, logger=LOG):
        outcome, store, model, _ = run(inputs([report("a", "AAPL")]), Broken())
    failed(outcome, store, "internal_error")
    assert model.calls == []
    assert "internal_error: RuntimeError" in caplog.text
    assert "SENTINEL" not in caplog.text


@pytest.mark.parametrize("error", [ValueError("boom"), ModelError("of no known kind")])
def test_an_unexpected_model_exception_is_an_internal_error(error):
    outcome, store, _, _ = run(
        inputs([report("a", "AAPL")]), market(("AAPL", "200")), model=FakeModel(error=error)
    )
    failed(outcome, store, "internal_error")


def test_an_input_that_leaves_no_candidate_within_the_limit_is_an_internal_error():
    outcome, store, model, _ = run(
        inputs([report("a", "AAPL")]),
        market(("AAPL", "200")),
        settings=Settings(max_input_chars=10),
    )
    failed(outcome, store, "internal_error")
    assert model.calls == []


# --- the database ------------------------------------------------------------------------


def test_a_failed_write_escapes_as_a_store_error_with_nothing_written():
    data = inputs([report("a", "AAPL")])
    store = FakePmStore(data, fail_write=True)
    with pytest.raises(StoreError):
        run(data, market(("AAPL", "200")), answer_of(decide()), store=store)
    assert store.writes == []


def test_a_failed_read_escapes_as_a_store_error_before_anything_else():
    data = inputs([report("a", "AAPL")])
    quotes = market(("AAPL", "200"))
    with pytest.raises(StoreError):
        run(data, quotes, answer_of(decide()), store=FakePmStore(data, fail_read=True))
    assert quotes.calls == []
