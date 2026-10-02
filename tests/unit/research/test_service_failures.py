"""A silent run and a broken run look different (specs/007-research-agent US3;
research R3, R8; analyze G1, G2, I1, T1)."""

from __future__ import annotations

import logging

import pytest

from tests.fakes.model import FakeModel
from tests.fakes.news import FakeNews
from tests.unit.research.support import Clock, article, config, messages, proposal
from tests.unit.research.test_service_happy import MemoryStore, make
from trading_agent.research import service
from trading_agent.research.ports import (
    KeyRejected,
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)

MARKER = "provider-body-marker-7f3a"  # must never reach a row or a log line
APPLE = article("apple", related=("AAPL",))
GOOD = FakeModel({"proposals": [proposal("AAPL", ids=["A1"])]})


def outcome_of(news=None, model=None, cfg=None, clock=None, caplog=None):
    store = MemoryStore()
    if caplog is not None:
        caplog.set_level(logging.INFO, logger="trading_agent.research")
    run, _ = make(news, model or FakeModel(GOOD.text), store, cfg, clock)
    outcome = run.run()
    assert len(store.writes) == 1 and store.writes[0]
    return outcome, store.writes[0]


def failure_row(rows, category):
    (row,) = rows
    assert row.direction == "no_action" and row.symbol is None
    assert row.rationale_md.startswith(f"Research run failed: {category}.")
    return row


@pytest.mark.parametrize("error", [RateLimited(MARKER), ProviderUnavailable(MARKER)])
def test_symbol_list_unavailable(error):
    model = FakeModel()
    outcome, rows = outcome_of(FakeNews(errors={"symbols": error}), model)
    assert outcome.failure == "symbol_list_unavailable"
    failure_row(rows, "symbol_list_unavailable")
    assert model.calls == []


@pytest.mark.parametrize("where", ["symbols", "general", "MSFT"])
def test_a_rejected_news_key_anywhere_is_news_unavailable(where):
    news = FakeNews(general=[APPLE], errors={where: KeyRejected(MARKER)})
    model = FakeModel()
    outcome, rows = outcome_of(news, model, config(watchlist=("MSFT", "NVDA")))
    assert outcome.failure == "news_unavailable"
    failure_row(rows, "news_unavailable")
    assert model.calls == []
    assert ("company", "NVDA") not in [c[:2] for c in news.calls]  # fetching stopped


def test_every_news_fetch_failing_is_news_unavailable():
    news = FakeNews(errors={"general": ProviderUnavailable(MARKER), "MSFT": RateLimited(MARKER)})
    model = FakeModel()
    outcome, rows = outcome_of(news, model, config(watchlist=("MSFT",)))
    assert outcome.failure == "news_unavailable"
    failure_row(rows, "news_unavailable")
    assert model.calls == []


def test_missing_general_news_continues_and_is_named():
    news = FakeNews(errors={"general": ProviderUnavailable()}, by_symbol={"AAPL": [APPLE]})
    outcome, rows = outcome_of(news, cfg=config(watchlist=("AAPL",)))
    assert outcome.failure is None and outcome.missing == ["general"]
    (row,) = rows
    assert row.symbol == "AAPL"
    assert row.rationale_md.endswith("\n\nMissing news: general.")


def test_missing_symbols_are_named_in_config_order():
    news = FakeNews(general=[APPLE], errors={"NVDA": RateLimited(), "MSFT": NotPermitted()})
    outcome, rows = outcome_of(news, cfg=config(watchlist=("MSFT", "NVDA")))
    assert outcome.failure is None
    assert rows[0].rationale_md.endswith("Missing news: MSFT, NVDA.")


def test_general_and_a_symbol_missing():
    news = FakeNews(
        errors={"general": ProviderUnavailable(), "MSFT": RateLimited()},
        by_symbol={"AAPL": [APPLE]},
    )
    _, rows = outcome_of(news, cfg=config(watchlist=("AAPL", "MSFT")))
    assert rows[0].rationale_md.endswith("Missing news: general; MSFT.")


def test_partial_news_with_nothing_to_argue_still_names_what_was_missing():
    news = FakeNews(general=[APPLE], errors={"MSFT": RateLimited()})
    _, rows = outcome_of(news, FakeModel({"proposals": []}), config(watchlist=("MSFT",)))
    (row,) = rows
    assert row.rationale_md == f"{service.NOTHING_TO_ARGUE}\n\nMissing news: MSFT."


def test_partial_news_and_a_model_failure_name_both():
    news = FakeNews(general=[APPLE], errors={"MSFT": RateLimited()})
    outcome, rows = outcome_of(
        news, FakeModel(error=ModelUnavailable()), config(watchlist=("MSFT",))
    )
    row = failure_row(rows, "model_unavailable")
    assert row.rationale_md.endswith("Missing news: MSFT.")


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ModelKeyRejected(MARKER), "model_key_rejected"),
        (ModelRejected(MARKER), "model_rejected_request"),
        (ModelUnavailable(MARKER), "model_unavailable"),
        (ModelRefused(MARKER), "model_refused"),
        (ModelTruncated(MARKER), "model_truncated"),
    ],
)
def test_model_failures(error, category, caplog):
    outcome, rows = outcome_of(model=FakeModel(error=error), caplog=caplog)
    assert outcome.failure == category
    failure_row(rows, category)
    assert f"research: {category}: {type(error).__name__}" in messages(caplog)
    assert MARKER not in "\n".join(messages(caplog)) + rows[0].rationale_md


def test_an_unexpected_error_still_leaves_a_row(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise ValueError(MARKER)

    monkeypatch.setattr(service, "select", broken)
    outcome, rows = outcome_of(caplog=caplog)
    assert outcome.failure == "internal_error"
    failure_row(rows, "internal_error")
    assert "research: internal_error: ValueError" in messages(caplog)
    assert MARKER not in caplog.text


def test_the_news_deadline_stops_fetching_and_names_the_rest():
    class SlowNews(FakeNews):
        def __init__(self, clock, **kw):
            super().__init__(**kw)
            self.clock = clock

        def company_news(self, symbol, start, end):
            self.clock.mono += 200  # each symbol's call is slow
            return super().company_news(symbol, start, end)

    clock = Clock()
    cfg = config(watchlist=("AAPL", "MSFT", "NVDA", "BRK.B"))  # budget (4+2)*(2+10) = 72 s
    news = SlowNews(clock, general=[APPLE], by_symbol={"AAPL": [APPLE]})
    store = MemoryStore()
    run, _ = make(news, FakeModel(GOOD.text), store, cfg, clock)
    outcome = run.run()
    fetched = [c[1] for c in news.calls if c[0] == "company"]
    assert fetched == ["AAPL"]
    assert outcome.missing == ["MSFT", "NVDA", "BRK.B"]
    assert store.writes[0][0].rationale_md.endswith("Missing news: MSFT, NVDA, BRK.B.")


def test_a_database_error_on_write_is_not_caught_here():
    class BrokenStore(MemoryStore):
        def write(self, rows):
            raise RuntimeError("database gone")

    run, _ = make(store=BrokenStore())
    with pytest.raises(RuntimeError):
        run.run()


@pytest.mark.parametrize(
    ("error", "suffix"),
    [
        (ModelKeyRejected(MARKER, status=401), " (HTTP 401)"),
        (ModelRejected(MARKER, status=400), " (HTTP 400)"),
        (ModelUnavailable(MARKER), ""),
    ],
)
def test_model_failures_log_the_http_status_but_never_the_message(error, suffix, caplog):
    outcome_of(model=FakeModel(error=error), caplog=caplog)
    logged = "\n".join(messages(caplog))
    assert f": {type(error).__name__}{suffix}\n" in logged + "\n"
    assert MARKER not in logged
