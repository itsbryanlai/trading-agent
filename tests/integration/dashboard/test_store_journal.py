"""Journal queries as the read role (specs/014 tasks T024)."""

from __future__ import annotations

import json
import secrets
from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from tests.unit.dashboard import attribution_fixtures as fx
from trading_agent.dashboard import attribution
from trading_agent.dashboard.store import PostgresStore, StoreError
from trading_agent.storage.migrate import apply_migrations

FIRST = date(2026, 10, 5)


@pytest.fixture(scope="module")
def world(make_database, server_url):
    url = make_database()
    apply_migrations(url)
    login, password = f"ta_test_dashboard_{secrets.token_hex(4)}", secrets.token_hex(8)
    admin = psycopg.connect(url, row_factory=dict_row, autocommit=True)
    admin.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE ta_dashboard").format(
            sql.Identifier(login), sql.Literal(password)
        )
    )
    for i in range(12):
        day = FIRST + timedelta(days=i)
        obj = fx.day(
            day.isoformat(), "0.010000", agents={"research": fx.book("2026-10-05", f"{100 + i}.0")}
        )
        admin.execute(
            "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md,"
            " per_agent_attribution) VALUES (%s, %s, %s, %s, %s::jsonb)",
            (day, 100000 + i, 100100 + i, f"Day {i}.", json.dumps(obj)),
        )
    yield {"store": PostgresStore(make_conninfo(url, user=login, password=password), 10)}
    admin.close()
    with psycopg.connect(server_url, autocommit=True) as server:
        server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login)))


def page(world, since=None, limit=51, offset=0):
    with world["store"].read() as reader:
        return reader.journal_page(since, limit, offset)


def full(world, since=None):
    with world["store"].read() as reader:
        return reader.journal_range(since)


def test_the_page_is_newest_first_with_exact_types(world):
    rows = page(world)
    assert [r["trading_day"] for r in rows] == [
        FIRST + timedelta(days=i) for i in range(11, -1, -1)
    ]
    top = rows[0]
    assert top["equity_open"] == Decimal("100011.00") and top["equity_close"] == Decimal(
        "100111.00"
    )
    assert top["summary_md"] == "Day 11."
    assert isinstance(top["per_agent_attribution"], dict)  # jsonb arrives parsed
    assert set(top) == {"trading_day", "equity_open", "equity_close", "summary_md",
                        "per_agent_attribution"}  # fmt: skip


def test_limit_and_offset_page_through_in_order(world):
    first, second, third = (
        page(world, limit=5, offset=0),
        page(world, limit=5, offset=5),
        page(world, limit=5, offset=10),
    )
    assert [len(first), len(second), len(third)] == [5, 5, 2]
    days = [r["trading_day"] for r in first + second + third]
    assert days == sorted(days, reverse=True) and len(set(days)) == 12
    assert page(world, limit=5, offset=50) == []


def test_since_includes_that_day_in_both_queries(world):
    since = FIRST + timedelta(days=9)
    assert [r["trading_day"] for r in page(world, since)] == [
        since + timedelta(days=d) for d in (2, 1, 0)
    ]
    assert [r["trading_day"] for r in full(world, since)] == [
        since + timedelta(days=d) for d in (0, 1, 2)
    ]


def test_the_range_query_is_every_row_oldest_first_and_slim(world):
    rows = full(world)
    assert [r["trading_day"] for r in rows] == [FIRST + timedelta(days=i) for i in range(12)]
    assert set(rows[0]) == {"trading_day", "equity_close", "per_agent_attribution"}
    assert [r["equity_close"] for r in rows[:3]] == [
        Decimal("100100"),
        Decimal("100101"),
        Decimal("100102"),
    ]


def test_none_means_the_whole_journal(world):
    assert len(page(world, None)) == 12 and len(full(world, None)) == 12
    assert page(world, date(2030, 1, 1)) == [] and full(world, date(2030, 1, 1)) == []


def test_stored_rows_parse_through_the_attribution_reader(world):
    parsed = [attribution.parse(r["per_agent_attribution"]) for r in full(world)]
    assert all(isinstance(p, attribution.Attribution) for p in parsed)
    assert parsed[3].agents["research"].index == Decimal("103.0")


def test_a_bound_since_cannot_inject(world):
    # `since` is a date; psycopg adapts it, so only a real date reaches the SQL.
    with pytest.raises(StoreError):
        page(world, "2026-10-05'; DROP TABLE journal; --")
    assert len(page(world)) == 12
