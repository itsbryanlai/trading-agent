"""Shared helpers for the reference-data unit tests: a fake clock that `sleep`
advances, and an in-memory ReferenceStore."""

from __future__ import annotations

from datetime import date

from tests.fakes.market_data import FakeMarketData
from trading_agent.reference.config import ReferenceConfig
from trading_agent.reference.normalize import ReferenceRow
from trading_agent.reference.service import ReferenceJob
from trading_agent.reference.symbols import Candidate


class Clock:
    """Monotonic seconds; `sleep` advances it, and each provider call costs `per_call`."""

    def __init__(self, per_call: float = 0.0) -> None:
        self.t = 0.0
        self.per_call = per_call
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds

    def on_call(self) -> float:
        now = self.t
        self.t += self.per_call
        return now


class MemoryStore:
    def __init__(self, candidates: list[Candidate] | None = None) -> None:
        self.candidates = list(candidates or [])
        self.rows: dict[tuple[str, date], ReferenceRow] = {}
        self.insert_error: dict[str, Exception] = {}

    def read_candidates(self) -> list[Candidate]:
        return list(self.candidates)

    def recorded_symbols(self, day: date) -> set[str]:
        return {symbol for symbol, d in self.rows if d == day}

    def insert(self, row: ReferenceRow, day: date) -> None:
        error = self.insert_error.get(row.symbol)
        if error is not None:
            raise error
        self.rows.setdefault((row.symbol, day), row)


def make_job(symbols=(), *, candidates=None, seeds=(), calls_per_minute=30, per_call=0.0):
    clock = Clock(per_call)
    fake = FakeMarketData(clock=clock.on_call)
    for symbol in symbols:
        fake.add(symbol)
    store = MemoryStore(
        candidates
        if candidates is not None
        else [Candidate(s, "position", None, None) for s in symbols]
    )
    config = ReferenceConfig(seed_symbols=tuple(seeds), calls_per_minute=calls_per_minute)
    job = ReferenceJob(fake, store, config, sleep=clock.sleep, monotonic=clock.monotonic)
    return job, fake, store, clock
