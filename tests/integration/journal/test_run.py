"""A whole journal run against Postgres, with fake quotes (specs/012 T017)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from psycopg.types.json import Jsonb

from tests.fakes.market_data import FakeMarketData
from tests.integration.helpers import as_role
from trading_agent.journal.config import JournalConfig
from trading_agent.journal.service import run
from trading_agent.journal.store import PgJournalStore

DAY = date(2026, 10, 9)  # EDT: open 13:30 UTC, close 20:00 UTC
NOW = datetime(2026, 10, 9, 22, 30, tzinfo=UTC)
CFG = JournalConfig(5, 20, 480, 5)
SOURCES = '[{"title": "t", "url": "https://example.com/x"}]'


def utc(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def seed(conn, *, snapshots=True):
    conn.execute(
        "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md, "
        "per_agent_attribution) VALUES (%s, 100000, 100000, 's', %s)",
        (date(2026, 10, 8), Jsonb({"schema_version": 1, "agents": {}})),
    )
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, "
        "sources, rationale_md, generated_at, expires_at) "
        "VALUES ('research', 'AAPL', 'buy', 3, 5, %s::jsonb, 'thesis', %s, %s)",
        (SOURCES, utc(9, 15), utc(10, 15)),
    )
    if snapshots:
        for at, equity in ((utc(9, 13, 30), "100000.00"), (utc(9, 19, 30), "100500.00")):
            conn.execute(
                "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
                "VALUES (%s, %s, 0, 0)",
                (at, equity),
            )


def journal_run(conn, **options):
    market = FakeMarketData(quote_time=utc(9, 19, 59))
    market.add("AAPL", current="229.15")
    with as_role(conn, "ta_journal"):
        return run(
            PgJournalStore(conn),
            market,
            CFG,
            now=NOW,
            sleep=lambda s: None,
            monotonic=lambda: 0.0,
            **options,
        )


def rows(conn):
    return conn.execute("SELECT * FROM journal ORDER BY trading_day").fetchall()


def test_a_full_run_writes_one_row_that_the_portfolio_manager_can_read(conn):
    seed(conn)
    outcome = journal_run(conn)
    assert outcome.status == "wrote"
    with as_role(conn, "ta_portfolio_manager"):
        row = conn.execute("SELECT * FROM journal WHERE trading_day = %s", (DAY,)).fetchone()
    assert (row["equity_open"], row["equity_close"]) == (Decimal("100000.00"), Decimal("100500.00"))
    assert row["summary_md"].startswith("## 2026-10-09\n")
    research = row["per_agent_attribution"]["agents"]["research"]
    assert research["holdings"]["AAPL"]["ref_price"] == "229.15"
    assert research["usage"]["written"] == 1


def test_a_second_plain_run_leaves_the_first_row_untouched(conn):
    seed(conn)
    assert journal_run(conn).status == "wrote"
    first = [r for r in rows(conn) if r["trading_day"] == DAY]
    second_outcome = journal_run(conn)
    assert (second_outcome.status, second_outcome.reason) == ("nothing_to_do", "already_written")
    assert [r for r in rows(conn) if r["trading_day"] == DAY] == first  # written_at included


def test_replace_rewrites_the_row_and_its_written_at(conn):
    seed(conn)
    journal_run(conn)
    conn.execute("UPDATE journal SET summary_md = 'old' WHERE trading_day = %s", (DAY,))
    before = [r for r in rows(conn) if r["trading_day"] == DAY][0]
    assert journal_run(conn, replace=True).status == "wrote"
    after = [r for r in rows(conn) if r["trading_day"] == DAY]
    assert len(after) == 1
    assert after[0]["summary_md"].startswith("## 2026-10-09\n")
    assert after[0]["written_at"] >= before["written_at"]


def test_a_run_with_no_snapshot_writes_nothing_and_leaves_earlier_rows(conn):
    seed(conn, snapshots=False)
    before = rows(conn)
    outcome = journal_run(conn)
    assert (outcome.status, outcome.reason) == ("failed", "no_account_snapshot")
    assert rows(conn) == before
