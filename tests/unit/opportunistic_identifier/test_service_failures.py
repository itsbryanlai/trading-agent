"""Every failure leaves exactly one no_action row naming its category (specs/011 US3; spec
FR-016-FR-018; contracts/oi-interface.md "Failure categories" and "Logs"). ERROR lines name
the exception type and the HTTP status, never its message."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from tests.fakes.model import FakeModel
from tests.fakes.oi_market_data import FakeOIMarketData
from tests.fakes.oi_store import FakeOIStore
from tests.unit.opportunistic_identifier.support import Clock, config
from tests.unit.opportunistic_identifier.test_service_happy import (
    STALE,
    answer_for,
    proposal,
    run,
)
from trading_agent.llm.ports import (
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
)
from trading_agent.opportunistic_identifier.ports import (
    KeyRejected,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)
from trading_agent.opportunistic_identifier.service import OIRun

SECRET = "SECRET-provider-text-do-not-log"
NAMES = ["AAA", "BBB", "CCC"]


def three() -> FakeOIMarketData:
    data = FakeOIMarketData()
    for symbol, price in zip(NAMES, ("190", "184", "196"), strict=True):
        data.add(symbol, current=price)
    return data


def error_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def assert_one_failure_row(outcome, store, category):
    assert outcome.failure == category
    (row,) = store.rows
    assert row.direction == "no_action" and row.symbol is None and row.sources == []
    assert row.conviction is None and row.suggested_size_pct is None
    assert row.rationale_md.startswith(f"Opportunistic Identifier run failed: {category}.")
    assert len(store.writes) == 1


# --- market data ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [ProviderUnavailable(SECRET), RateLimited(SECRET), NotPermitted()]
)
def test_a_symbol_list_that_cannot_be_fetched_is_symbol_list_unavailable(error, caplog):
    data = three()
    data.fail("us_listings", error=error)
    outcome, data, model, store, _ = run(data, universe=NAMES)
    assert_one_failure_row(outcome, store, "symbol_list_unavailable")
    assert model.calls == [] and data.calls == [("us_listings", None)]
    assert error_lines(caplog) == [
        f"opportunistic_identifier: symbol_list_unavailable: {type(error).__name__}"
    ]


def test_a_rejected_key_on_the_symbol_list_is_market_data_unavailable(caplog):
    data = three()
    data.fail("us_listings", error=KeyRejected(SECRET))
    outcome, _, model, store, _ = run(data, universe=NAMES)
    assert_one_failure_row(outcome, store, "market_data_unavailable")
    assert model.calls == []
    assert error_lines(caplog) == ["opportunistic_identifier: market_data_unavailable: KeyRejected"]


@pytest.mark.parametrize(
    ("call", "symbol"),
    [("quote", "AAA"), ("quote", "CCC"), ("profile", "BBB"), ("fundamentals", "CCC")],
)
def test_a_rejected_key_on_any_per_name_call_fails_the_run(call, symbol, caplog):
    data = three()
    data.fail(call, symbol, error=KeyRejected(SECRET))
    outcome, _, model, store, _ = run(data, universe=NAMES)
    assert_one_failure_row(outcome, store, "market_data_unavailable")
    assert model.calls == []
    assert error_lines(caplog) == ["opportunistic_identifier: market_data_unavailable: KeyRejected"]


@pytest.mark.parametrize(
    "error", [ProviderUnavailable(SECRET), NotPermitted(), RateLimited(SECRET)]
)
def test_every_per_name_fetch_failing_is_market_data_unavailable(error):
    data = three()
    data.fail("quote", error=error)
    outcome, _, model, store, _ = run(data, universe=NAMES)
    assert_one_failure_row(outcome, store, "market_data_unavailable")
    assert model.calls == []


def test_a_mix_of_fetch_errors_on_every_name_is_market_data_unavailable():
    data = three()
    data.fail("quote", "AAA", error=ProviderUnavailable())
    data.fail("profile", "BBB", error=NotPermitted())
    data.fail("fundamentals", "CCC", error=RateLimited())
    outcome, _, _, store, _ = run(data, universe=NAMES)
    assert_one_failure_row(outcome, store, "market_data_unavailable")


@pytest.mark.parametrize("error", [ProviderUnavailable(), NotPermitted(), RateLimited()])
def test_partial_data_does_not_fail_the_run(error, caplog):
    data = three()
    data.fail("quote", "BBB", error=error)
    data.fail("profile", "CCC", error=error)
    with caplog.at_level(logging.INFO):
        outcome, _, model, store, _ = run(data, answer_for("AAA"), universe=NAMES)
    assert outcome.failure is None and [r.symbol for r in store.rows] == ["AAA"]
    assert sum(outcome.counts.skipped.values()) == 2 and len(model.calls) == 1
    assert error_lines(caplog) == []


def test_stale_quotes_are_not_fetch_failures():
    data = FakeOIMarketData()
    data.add("AAA", quote_time=STALE)
    data.add("BBB", quote_time=STALE)
    outcome, _, model, store, _ = run(data, universe=["AAA", "BBB"])
    assert outcome.failure is None and outcome.note == "empty_shortlist" and model.calls == []
    assert store.rows[0].direction == "no_action"


def test_names_stopped_by_the_listing_check_are_not_fetch_failures():
    outcome, _, _, _, _ = run(three(), universe=["GHST", "NOPE"])
    assert outcome.failure is None and outcome.note == "empty_shortlist"


def test_the_failure_row_carries_the_run_counts_and_the_days_close():
    data = three()
    data.fail("quote", error=ProviderUnavailable())
    outcome, _, _, store, _ = run(data, universe=NAMES)
    (row,) = store.rows
    assert "3 in slice, 0 fetched" in row.rationale_md
    assert "skipped (provider_unavailable: 3)" in row.rationale_md
    assert row.expires_at.isoformat() == "2026-10-08T20:00:00+00:00"


# --- input size, the model and the answer -------------------------------------------------------


def test_a_document_over_max_input_chars_is_input_too_large(caplog):
    outcome, _, model, store, _ = run(three(), universe=NAMES, max_input_chars=500)
    assert_one_failure_row(outcome, store, "input_too_large")
    assert model.calls == []
    assert error_lines(caplog) == [
        "opportunistic_identifier: input_too_large: input over the limit"
    ]


MODEL_CASES = [
    (ModelKeyRejected, "model_key_rejected"),
    (ModelRejected, "model_rejected_request"),
    (ModelUnavailable, "model_unavailable"),
    (ModelRefused, "model_refused"),
    (ModelTruncated, "model_truncated"),
]


@pytest.mark.parametrize(("error_class", "category"), MODEL_CASES)
def test_each_model_error_is_its_own_category_without_the_message(error_class, category, caplog):
    outcome, store = _run_with_model(FakeModel(error=error_class(SECRET)))
    assert_one_failure_row(outcome, store, category)
    assert error_lines(caplog) == [f"opportunistic_identifier: {category}: {error_class.__name__}"]
    assert SECRET not in caplog.text and SECRET not in store.rows[0].rationale_md


@pytest.mark.parametrize(("error_class", "category"), MODEL_CASES)
def test_the_http_status_is_logged_when_there_is_one(error_class, category, caplog):
    _run_with_model(FakeModel(error=error_class(SECRET, status=503)))
    assert error_lines(caplog) == [
        f"opportunistic_identifier: {category}: {error_class.__name__} (HTTP 503)"
    ]


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", "null", '{"proposals": "x"}', '{"proposals": [], "extra": 1}', ""],
)
def test_an_unusable_answer_is_a_failure(text, caplog):
    outcome, store = _run_with_model(FakeModel(text))
    assert_one_failure_row(outcome, store, "unusable_answer")
    assert error_lines(caplog) == ["opportunistic_identifier: unusable_answer: answer not usable"]


def test_a_usable_answer_with_every_proposal_dropped_is_not_a_failure():
    outcome, store = _run_with_model(FakeModel({"proposals": [proposal("ZZZ")]}))
    assert outcome.failure is None and outcome.note == "all_dropped"


def _run_with_model(model):
    data, store, clock = three(), FakeOIStore(), Clock()
    outcome = OIRun(
        data, model, store, config(NAMES), clock=clock, sleep=clock.sleep, monotonic=clock.monotonic
    ).run()
    return outcome, store


# --- an unexpected error --------------------------------------------------------------------------


@dataclass
class Boom:
    error: Exception

    def complete(self, system, user, schema):
        raise self.error


class ExplodingMarket(FakeOIMarketData):
    def profile(self, symbol):
        raise RuntimeError(SECRET)


def test_an_unexpected_error_in_the_fetch_is_internal_error(caplog):
    data = ExplodingMarket()
    data.add("AAA")
    outcome, _, model, store, _ = run(data, universe=["AAA"])
    assert_one_failure_row(outcome, store, "internal_error")
    assert model.calls == []
    assert error_lines(caplog) == ["opportunistic_identifier: internal_error: RuntimeError"]
    assert SECRET not in caplog.text and SECRET not in store.rows[0].rationale_md


def test_an_unexpected_error_from_the_model_client_is_internal_error(caplog):
    outcome, store = _run_with_model(Boom(ValueError(SECRET)))
    assert_one_failure_row(outcome, store, "internal_error")
    assert error_lines(caplog) == ["opportunistic_identifier: internal_error: ValueError"]


def test_each_failure_writes_exactly_one_row_in_one_write():
    for data in (_failing("quote", KeyRejected()), _failing("us_listings", ProviderUnavailable())):
        _, _, _, store, _ = run(data, universe=NAMES)
        assert len(store.writes) == 1 and len(store.writes[0]) == 1


def _failing(call, error):
    data = three()
    data.fail(call, error=error)
    return data


def test_the_failed_run_still_reads_the_open_symbols_first():
    store = FakeOIStore({"AAA"})
    run(_failing("quote", KeyRejected()), universe=NAMES, store=store)
    assert len(store.reads) == 1
