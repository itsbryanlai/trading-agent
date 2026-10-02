"""One run, the happy path (specs/007-research-agent US1; research R3, R8)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.fakes.model import FakeModel
from tests.fakes.news import FakeNews
from tests.unit.research.support import (
    EARLY_CLOSE,
    EARLY_CLOSE_DAY_0830,
    HOLIDAY_0830,
    SAT_0830,
    THU_0830,
    THU_CLOSE,
    Clock,
    article,
    config,
    messages,
    proposal,
)
from trading_agent.research.answer import ANSWER_SCHEMA
from trading_agent.research.service import NOTHING_TO_ARGUE, ResearchRun


class MemoryStore:
    def __init__(self, open_reports=()):
        self.open = list(open_reports)
        self.writes: list[list] = []
        self.reads: list[datetime] = []

    def open_reports(self, now):
        self.reads.append(now)
        return list(self.open)

    def write(self, rows):
        self.writes.append(list(rows))


def make(news=None, model=None, store=None, cfg=None, clock=None, dry_run=False):
    clock = clock or Clock()
    run = ResearchRun(
        news or FakeNews(general=[article("apple", related=("AAPL",))]),
        model or FakeModel({"proposals": [proposal("AAPL", ids=["A1"])]}),
        store or MemoryStore(),
        cfg or config(),
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        dry_run=dry_run,
    )
    return run, clock


def test_a_valid_answer_writes_its_rows_in_one_write():
    news = FakeNews(
        general=[article("apple", related=("AAPL",))],
        by_symbol={"MSFT": [article("micro")]},
    )
    model = FakeModel(
        {
            "proposals": [
                proposal("AAPL", "buy", 4, 5, ["A1"]),
                proposal("MSFT", "sell", 2, 0, ["A2"]),
            ]
        }
    )
    store = MemoryStore()
    run, _ = make(news, model, store, config(watchlist=("MSFT",)))
    outcome = run.run()
    assert outcome.failure is None and len(store.writes) == 1
    aapl, msft = store.writes[0]
    assert (aapl.symbol, aapl.direction, aapl.conviction, aapl.suggested_size_pct) == (
        "AAPL",
        "buy",
        4,
        Decimal(5),
    )
    assert (msft.symbol, msft.direction, msft.suggested_size_pct) == ("MSFT", "sell", Decimal(0))
    assert aapl.sources[0]["url"] == "https://news.example.com/apple"
    assert {r.expires_at for r in store.writes[0]} == {THU_CLOSE}


def test_reports_on_an_early_close_day_expire_at_the_early_close():
    store = MemoryStore()
    clock = Clock(EARLY_CLOSE_DAY_0830)
    news = FakeNews(general=[article("apple", related=("AAPL",), at=EARLY_CLOSE_DAY_0830)])
    run, _ = make(news, store=store, clock=clock)
    run.run()
    assert store.writes[0][0].expires_at == EARLY_CLOSE


def test_an_empty_answer_writes_one_no_action():
    store = MemoryStore()
    run, _ = make(model=FakeModel({"proposals": []}), store=store)
    outcome = run.run()
    assert outcome.failure is None
    (row,) = store.writes[0]
    assert (row.symbol, row.direction, row.conviction, row.suggested_size_pct, row.sources) == (
        None,
        "no_action",
        None,
        None,
        [],
    )
    assert row.rationale_md == NOTHING_TO_ARGUE


def test_no_news_in_the_window_skips_the_model():
    model = FakeModel()
    store = MemoryStore()
    run, _ = make(FakeNews(general=[]), model, store)
    run.run()
    assert model.calls == []
    assert store.writes[0][0].direction == "no_action"


def test_the_model_gets_the_selected_ids_and_the_schema():
    model = FakeModel()
    news = FakeNews(general=[article("a"), article("b", at=THU_0830 - timedelta(hours=3))])
    run, _ = make(news, model)
    run.run()
    (call,) = model.calls
    assert call["schema"] == ANSWER_SCHEMA
    assert '"id": "A1"' in call["user"] and '"id": "A2"' in call["user"]


def test_open_reports_are_read_and_passed_to_the_model():
    store = MemoryStore(open_reports=[("MSFT", "sell")])
    model = FakeModel()
    run, _ = make(model=model, store=store)
    run.run()
    assert store.reads == [THU_0830]
    assert '"symbol": "MSFT"' in model.calls[0]["user"]


def test_company_news_is_requested_per_symbol_with_the_window_dates():
    news = FakeNews(general=[article("g")])
    run, _ = make(news, cfg=config(watchlist=("MSFT", "NVDA")))
    run.run()
    company = [c for c in news.calls if c[0] == "company"]
    assert company == [
        ("company", "MSFT", date(2026, 9, 30), date(2026, 10, 1)),
        ("company", "NVDA", date(2026, 9, 30), date(2026, 10, 1)),
    ]
    assert news.calls[0] == ("symbols",)


def test_calls_are_paced():
    run, clock = make(cfg=config(watchlist=("MSFT", "NVDA"), finnhub_calls_per_minute=30))
    run.run()
    # symbols, general, MSFT, NVDA: a 2 s pause before each call after the first.
    assert clock.slept == [2.0, 2.0, 2.0]


def test_the_run_logs_its_counts(caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.research")
    model = FakeModel(
        {"proposals": [proposal("AAPL", ids=["A1"])]}, input_tokens=1500, output_tokens=200
    )
    run, _ = make(model=model)
    run.run()
    text = "\n".join(messages(caplog))
    assert "run started (prompt v0.2, provider qwen, model qwen3.7-plus)" in text
    assert "1 articles in window, 1 sent (" in text and "missing: none" in text
    assert "model used 1500 input and 200 output tokens" in text
    assert "1 proposals received, 1 accepted, 0 dropped" in text
    assert "wrote 1 report(s)" in text


@pytest.mark.parametrize(
    "now",
    [
        SAT_0830,
        HOLIDAY_0830,
        THU_CLOSE - timedelta(minutes=1),
        THU_CLOSE,
        datetime(2026, 10, 1, 21, 0, tzinfo=UTC),
    ],
)
def test_outside_the_window_nothing_is_fetched_or_written(now):
    news, store = FakeNews(), MemoryStore()
    run, _ = make(news, store=store, clock=Clock(now))
    outcome = run.run()
    assert outcome.skipped and outcome.rows == []
    assert news.calls == [] and store.writes == [] and store.reads == []


def test_just_inside_the_window_still_runs():
    store = MemoryStore()
    run, _ = make(store=store, clock=Clock(THU_CLOSE - timedelta(minutes=1, seconds=1)))
    assert not run.run().skipped
    assert len(store.writes) == 1


def test_the_log_counts_articles_tagged_with_a_ticker(caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.research")
    news = FakeNews(
        general=[article("tagged", related=("AAPL",)), article("untagged", related=())],
        by_symbol={"MSFT": [article("feed")]},  # tagged by its own feed
    )
    run, _ = make(news, cfg=config(watchlist=("MSFT",)))
    outcome = run.run()
    assert outcome.tagged_articles == 2
    assert "3 sent (2 tagged with a ticker;" in "\n".join(messages(caplog))
