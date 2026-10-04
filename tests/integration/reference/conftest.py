"""Reference-data integration helpers, on the test clock: Monday 2026-09-28,
08:30 ET (12:30 UTC) for the job, 14:00 UTC for gate evaluations (the gate
rejects market_closed before any other rule; /speckit-analyze F1)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tests.fakes.market_data import FakeMarketData
from tests.integration.helpers import as_role
from tests.integration.storage.chain import SOURCES
from tests.unit.reference.support import Clock
from trading_agent.reference.config import ReferenceConfig
from trading_agent.reference.service import PgReferenceStore, ReferenceJob

JOB_NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)
GATE_NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
TODAY = date(2026, 9, 28)


def add_report(conn, symbol, generated_at, expires_at) -> None:
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources, "
        "rationale_md, generated_at, expires_at) VALUES ('research', %s, 'buy', 3, 5, %s::jsonb, "
        "'t', %s, %s)",
        (symbol, SOURCES, generated_at, expires_at),
    )


def add_decision(conn, symbol, generated_at) -> None:
    conn.execute(
        "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision, "
        "generated_at, quote_time) VALUES (%s, 'buy', 5, 'r', 100, %s, %s)",
        (symbol, generated_at, generated_at),
    )


def add_position(conn, symbol, qty=10, avg_entry="200") -> None:
    conn.execute(
        "INSERT INTO positions (symbol, qty, avg_entry_price) VALUES (%s, %s, %s)",
        (symbol, qty, avg_entry),
    )


def rows_for(conn, day=TODAY) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT * FROM instrument_reference WHERE trading_day = %s ORDER BY symbol", (day,)
    ).fetchall()
    return {r["symbol"]: r for r in rows}


class Harness:
    """A job wired to the test connection as ta_reference_data, and a fake provider."""

    def __init__(self, conn, seeds=(), calls_per_minute=30, per_call=0.0) -> None:
        self.conn = conn
        self.clock = Clock(per_call)
        self.fake = FakeMarketData(clock=self.clock.on_call)
        self.job = ReferenceJob(
            self.fake,
            PgReferenceStore(conn, _allow_savepoints=True),
            ReferenceConfig(seed_symbols=tuple(seeds), calls_per_minute=calls_per_minute),
            sleep=self.clock.sleep,
            monotonic=self.clock.monotonic,
        )

    def tick(self, now):
        with as_role(self.conn, "ta_reference_data"):
            return self.job.tick(now)


@pytest.fixture
def harness(conn):
    return Harness(conn)
