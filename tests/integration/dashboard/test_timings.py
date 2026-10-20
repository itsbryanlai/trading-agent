"""Every store method on a seeded year (specs/014 tasks T031; SC-007).

About 3,500 snapshots, 3,000 reports, 1,500 decisions and verdicts (with orders and refusals),
5,000 orchestrator runs and 250 journal rows, then each method in contracts/queries.md as the
read role, worst of three runs, under 0.5 s on the throwaway database. If one is slower the
answer is flagged to the owner, not an index added here.
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from tests.unit.dashboard import attribution_fixtures as fx
from trading_agent.dashboard.store import PostgresStore
from trading_agent.storage.migrate import apply_migrations

LIMIT_SECONDS = 0.5
RUNS = 3
NOW = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)
YEAR_AGO = NOW - timedelta(days=365)
PAGE = 50

COUNTS = {
    "account_snapshots": 3500,
    "reports": 3000,
    "decisions": 1500,
    "risk_verdicts": 1500,
    "orders": 500,
    "execution_refusals": 200,
    "orchestrator_runs": 5000,
    "journal": 250,
}


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
    _seed(admin)
    admin.execute("ANALYZE")
    yield {
        "admin": admin,
        "store": PostgresStore(make_conninfo(url, user=login, password=password), 10),
    }
    admin.close()
    with psycopg.connect(server_url, autocommit=True) as server:
        server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login)))


def _spread(i: str, count: int) -> str:
    """SQL for a time spread evenly over the last year; `i` is the series column."""
    return f"timestamptz '{YEAR_AGO.isoformat()}' + interval '365 days' * ({i}::numeric / {count})"


def _seed(conn) -> None:
    conn.execute(
        "INSERT INTO positions (symbol, qty, avg_entry_price) "
        "SELECT 'P' || i, i, 10 + i FROM generate_series(1, 20) i"
    )
    conn.execute(
        f"INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
        f"SELECT {_spread('i', 3500)}, 100000 + i, 50000, 90000 "
        f"FROM generate_series(1, 3500) i"
    )
    conn.execute(
        f"INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources,"
        f" rationale_md, generated_at, expires_at) "
        f"SELECT CASE WHEN i % 2 = 0 THEN 'research' ELSE 'opportunistic_identifier' END,"
        f" 'S' || (i % 60), 'buy', 3, 5,"
        f' \'[{{"title": "t", "url": "https://news.example/a", "publisher": "p"}}]\'::jsonb,'
        f" 'A rationale paragraph. ' || repeat('word ', 80), {_spread('i', 3000)},"
        f" {_spread('i', 3000)} + interval '1 day' FROM generate_series(1, 3000) i"
    )
    conn.execute(
        f"INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision,"
        f" quote_time, generated_at) SELECT 'S' || (i % 60), 'buy', 5,"
        f" 'Reasoning. ' || repeat('word ', 80), 100, {_spread('i', 1500)}, {_spread('i', 1500)}"
        f" FROM generate_series(1, 1500) i"
    )
    conn.execute(
        "INSERT INTO decision_reports (decision_id, report_id) "
        "SELECT d.id, r.id FROM (SELECT id, row_number() OVER (ORDER BY generated_at) n "
        "FROM decisions) d JOIN (SELECT id, row_number() OVER (ORDER BY generated_at) n "
        "FROM reports) r ON r.n IN (d.n * 2, d.n * 2 - 1)"
    )
    # Verdicts: the first 700 approved (500 become orders, 200 refusals), the rest rejected.
    conn.execute(
        "INSERT INTO risk_verdicts (decision_id, evaluated_at, verdict, rejection_rule,"
        " approved_order, trading_day, config_version) "
        "SELECT id, generated_at + interval '1 minute',"
        " CASE WHEN n <= 700 THEN 'approved' ELSE 'rejected' END,"
        " CASE WHEN n <= 700 THEN NULL ELSE 'max_position_pct' END,"
        " CASE WHEN n <= 700 THEN jsonb_build_object('symbol', symbol, 'side', 'buy', 'qty', 10,"
        " 'limit_price', 100, 'time_in_force', 'day') END,"
        " (generated_at AT TIME ZONE 'America/New_York')::date, 'test' "
        "FROM (SELECT d.*, row_number() OVER (ORDER BY generated_at) n FROM decisions d) d"
    )
    conn.execute(
        "INSERT INTO orders (id, risk_verdict_id, status, limit_price, submitted_at) "
        "SELECT '2026-01-01-' || d.symbol || '-buy-' || left(v.id::text, 8), v.id, 'filled', 100, "
        "v.evaluated_at FROM risk_verdicts v JOIN decisions d ON d.id = v.decision_id "
        "WHERE v.verdict = 'approved' ORDER BY v.evaluated_at LIMIT 500"
    )
    conn.execute(
        "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
        "SELECT v.id, 'quote_above_ceiling', '{\"ceiling\": \"1\"}'::jsonb, v.evaluated_at "
        "FROM risk_verdicts v WHERE v.verdict = 'approved' AND NOT EXISTS "
        "(SELECT 1 FROM orders o WHERE o.risk_verdict_id = v.id)"
    )
    conn.execute(
        f"INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, slot_at,"
        f" started_at, finished_at, outcome, detail) "
        f"SELECT (ARRAY['research', 'opportunistic_identifier', 'portfolio_manager'])[1 + i % 3],"
        f" (timestamptz '{YEAR_AGO.isoformat()}')::date + (i % 365), 'scheduled', 'slot' || i,"
        f" {_spread('i', 5000)},"
        f" CASE WHEN i % 20 = 0 THEN NULL ELSE {_spread('i', 5000)} END,"
        f" CASE WHEN i % 20 = 0 THEN NULL ELSE {_spread('i', 5000)} + interval '3 minutes' END,"
        f" CASE WHEN i % 20 = 0 THEN 'skipped' WHEN i % 15 = 0 THEN 'failed' ELSE 'succeeded' END,"
        f" CASE WHEN i % 20 = 0 THEN 'trading paused' END FROM generate_series(1, 5000) i"
    )
    start = date(2026, 10, 19) - timedelta(days=250)
    for i in range(250):
        day = start + timedelta(days=i)
        obj = fx.day(
            day.isoformat(),
            "0.001000",
            agents={
                "research": fx.book(start.isoformat(), f"{100 + i * 0.1:.8f}"),
                "opportunistic_identifier": fx.book(start.isoformat(), f"{100 + i * 0.05:.8f}"),
            },
        )
        conn.execute(
            "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md,"
            " per_agent_attribution) VALUES (%s, 100000, 100100, %s, %s::jsonb)",
            (day, "Summary. " + "word " * 60, json.dumps(obj)),
        )


def test_the_seed_has_the_size_of_a_year(world):
    admin = world["admin"]
    for table, expected in COUNTS.items():
        got = admin.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table)))
        n = got.fetchone()["n"]
        assert n == expected, (table, n)


def _measure(world, call) -> float:
    worst = 0.0
    for _ in range(RUNS):
        started = time.perf_counter()
        with world["store"].read() as reader:
            call(reader)
        worst = max(worst, time.perf_counter() - started)
    return worst


def _calls(world) -> dict:
    admin = world["admin"]
    some_reports = [r["id"] for r in admin.execute("SELECT id FROM reports LIMIT 50").fetchall()]
    week = NOW - timedelta(days=7)
    deep_offset = PAGE * 29  # the 30th page of a list
    deep = deep_offset + PAGE + 1
    one_day = (date(2026, 10, 18), date(2026, 10, 19))
    return {
        "state": lambda r: r.state(),
        "positions": lambda r: r.positions(),
        "latest_snapshot": lambda r: r.latest_snapshot(),
        "equity_points 1w (every point)": lambda r: r.equity_points(week, False),
        "equity_points 1m (daily)": lambda r: r.equity_points(NOW - timedelta(days=30), True),
        "equity_points all (daily)": lambda r: r.equity_points(None, True),
        "equity_points all (every point)": lambda r: r.equity_points(None, False),
        "latest_pm_run": lambda r: r.latest_pm_run(),
        "ping": lambda r: r.ping(),
        "memberships": lambda r: r.memberships(),
        "orders page 1": lambda r: r.orders(None, None, None, PAGE + 1, 0),
        "orders page 30": lambda r: r.orders(None, None, None, PAGE + 1, deep_offset),
        "orders by symbol": lambda r: r.orders("S7", None, None, PAGE + 1, 0),
        "orders by dates": lambda r: r.orders(None, week - timedelta(days=60), week, PAGE + 1, 0),
        "reports page 1": lambda r: r.reports(None, None, None, None, PAGE + 1),
        "reports page 30": lambda r: r.reports(None, None, None, None, deep),
        "reports by agent and symbol": lambda r: r.reports("research", "S7", None, None, deep),
        "decisions page 1": lambda r: r.decisions(None, None, None, PAGE + 1),
        "decisions page 30": lambda r: r.decisions(None, None, None, deep),
        "decisions by symbol": lambda r: r.decisions("S7", None, None, deep),
        "runs page 1": lambda r: r.runs(None, None, None, False, PAGE + 1),
        "runs page 30": lambda r: r.runs(None, None, None, False, deep),
        "runs problems only": lambda r: r.runs(None, None, None, True, deep),
        "runs by agent": lambda r: r.runs("portfolio_manager", None, None, False, deep),
        "report_times (50 ids)": lambda r: r.report_times(some_reports),
        "journal_page 1": lambda r: r.journal_page(None, PAGE + 1, 0),
        "journal_page last": lambda r: r.journal_page(None, PAGE + 1, 200),
        "journal_range all": lambda r: r.journal_range(None),
        "journal_range 1m": lambda r: r.journal_range(date(2026, 9, 19)),
        "orders one day": lambda r: r.orders(
            None, *[datetime.combine(d, datetime.min.time(), UTC) for d in one_day], PAGE + 1, 0
        ),
    }


def test_every_store_method_returns_in_under_half_a_second_on_a_seeded_year(world, capsys):
    timings = {name: _measure(world, call) for name, call in _calls(world).items()}
    report = "\n".join(f"{seconds * 1000:8.1f} ms  {name}" for name, seconds in timings.items())
    with capsys.disabled():
        print("\nT031 timings (worst of 3, ms):\n" + report)
    slow = {
        name: round(seconds, 3) for name, seconds in timings.items() if seconds >= LIMIT_SECONDS
    }
    assert not slow, f"over {LIMIT_SECONDS} s (flag to the owner, do not add an index): {slow}"


def test_a_whole_page_worth_of_queries_in_one_transaction_stays_fast(world):
    """What `/activity` at page 30 asks for in one `read()`, as the route does."""
    deep = PAGE * 29 + PAGE + 1

    def page(reader):
        reader.state()
        reader.reports(None, None, None, None, deep)
        reader.decisions(None, None, None, deep)
        reader.runs(None, None, None, False, deep)

    assert _measure(world, page) < LIMIT_SECONDS
