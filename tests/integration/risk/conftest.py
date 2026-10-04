"""Seeding helpers for Risk Gate integration tests, on the test clock:
Monday 2026-09-28, market open at 13:30 UTC, "now" 14:00 UTC (10:00 ET).
All rows are inserted as the admin inside the rolled-back `conn` fixture.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from tests.integration.storage.chain import SOURCES

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
TODAY = date(2026, 9, 28)
PRE_OPEN = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)  # 08:00 ET
INTRADAY = datetime(2026, 9, 28, 13, 45, tzinfo=UTC)  # 09:45 ET
REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "risk.yaml"


def insert_snapshot(conn, taken_at, equity="100000", cash="100000") -> None:
    conn.execute(
        "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
        "VALUES (%s, %s, %s, %s)",
        (taken_at, equity, cash, cash),
    )


def insert_reference(conn, symbol="AAPL", day=TODAY, **overrides) -> None:
    row = {
        "security_type": "common_stock",
        "exchange_mic": "XNAS",
        "market_cap_usd": Decimal("3000000000000"),
        "avg_daily_dollar_volume_usd": Decimal("5000000000"),
        "share_price_usd": Decimal("200"),
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO instrument_reference (symbol, trading_day, security_type, exchange_mic, "
        "market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (symbol, day, *row.values()),
    )


def insert_position(conn, symbol="AAPL", qty=50, avg_entry="200") -> None:
    conn.execute(
        "INSERT INTO positions (symbol, qty, avg_entry_price) VALUES (%s, %s, %s)",
        (symbol, qty, avg_entry),
    )


def make_decision(conn, direction="buy", target="5", quote="200", symbol="AAPL") -> str:
    report_id = conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources, "
        "rationale_md, generated_at, expires_at) VALUES ('research', %s, 'buy', 4, 5, %s::jsonb, "
        "'t', now() - interval '1 hour', now() + interval '6 hours') RETURNING id",
        (symbol, SOURCES),
    ).fetchone()["id"]
    decision_id = conn.execute(
        "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision, "
        "quote_time) VALUES (%s, %s, %s, 'r', %s, now()) RETURNING id",
        (symbol, direction, target, quote),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO decision_reports (decision_id, report_id) VALUES (%s, %s)",
        (decision_id, report_id),
    )
    return decision_id


def seed_open_day(conn: psycopg.Connection, equity="100000", cash="100000") -> None:
    """A normal trading morning: pre-open and intraday snapshots, AAPL in the universe."""
    insert_snapshot(conn, PRE_OPEN, equity, cash)
    insert_snapshot(conn, INTRADAY, equity, cash)
    insert_reference(conn)


def verdict_count(conn) -> int:
    return conn.execute("SELECT count(*) AS n FROM risk_verdicts").fetchone()["n"]


@pytest.fixture
def repo_config() -> Path:
    return REPO_CONFIG
