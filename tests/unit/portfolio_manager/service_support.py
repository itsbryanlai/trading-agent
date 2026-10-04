"""Harness for the service tests: a clock that sleeps by advancing, settings, and a runner."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tests.fakes.market_data import FakeMarketData
from tests.fakes.model import FakeModel
from tests.fakes.pm_store import FakePmStore
from tests.unit.portfolio_manager.support import NOW
from trading_agent.llm.settings import ModelSettings
from trading_agent.portfolio_manager import service


class Clock:
    """`clock()` is the current time; `sleep(s)` moves it on (and records the sleep)."""

    def __init__(self, start: datetime = NOW) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass(frozen=True)
class Settings:
    quote_max_age_minutes: int = 5
    finnhub_calls_per_minute: int = 30
    quote_phase_seconds: int = 90
    journal_entries: int = 5
    journal_summary_max_chars: int = 2000
    rationale_max_chars: int = 2000
    reasoning_max_chars: int = 2000
    max_input_chars: int = 300_000
    model: ModelSettings = field(
        default_factory=lambda: ModelSettings("qwen", "qwen3.7-plus", 8000, 150, "medium")
    )


def market(*prices: tuple[str, str], at: datetime | None = None) -> FakeMarketData:
    """Fake quotes `(symbol, price)`, each traded a minute before NOW unless `at` is given."""
    fake = FakeMarketData()
    for symbol, price in prices:
        fake.add(symbol, current=price, quote_time=at or NOW - timedelta(minutes=1))
    return fake


def answer_of(*decisions: dict) -> dict:
    return {"decisions": list(decisions)}


def decide(symbol="AAPL", direction="buy", target=4, ids=("R1",), reasoning="Because."):
    return {
        "symbol": symbol,
        "direction": direction,
        "target_weight_pct": target,
        "reasoning": reasoning,
        "report_ids": list(ids),
    }


def run(inputs, quotes, model_answer=None, *, clock=None, settings=None, store=None, model=None):
    clock = clock or Clock()
    store = store or FakePmStore(inputs)
    model = model or FakeModel(model_answer if model_answer is not None else answer_of())
    outcome = service.run(
        clock=clock,
        config=settings or Settings(),
        store=store,
        quotes=quotes,
        model=model,
        sleep=clock.sleep,
    )
    return outcome, store, model, clock


def model_input(model: FakeModel) -> dict:
    return json.loads(model.calls[0]["user"])
