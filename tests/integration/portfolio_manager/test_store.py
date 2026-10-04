"""PostgresStore as ta_portfolio_manager (specs/008-portfolio-manager research P6, P10;
data-model.md "Reads"). Times use the next real session, so database `now()` never
passes a row's expiry."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.integration.helpers import as_role, attempt
from trading_agent.portfolio_manager.answer import CheckedDecision
from trading_agent.portfolio_manager.store import PostgresStore, StoreError
from trading_agent.risk import calendar
from trading_agent.storage.migrate import apply_migrations

ROLE = "ta_portfolio_manager"
NEW_YORK = ZoneInfo("America/New_York")


def next_session() -> date:
    day = datetime.now(UTC).date() + timedelta(days=1)
    while not calendar.is_session(day):
        day += timedelta(days=1)
    return day


DAY = next_session()
RUN_START = calendar.open_time(DAY) + timedelta(minutes=30)  # 10:00 ET
CLOSE = calendar.close_time(DAY)


def add_report(
    conn,
    symbol,
    direction="buy",
    *,
    agent="research",
    generated=None,
    expires=None,
    size="5",
    rationale="thesis",
):
    row = conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct,"
        " sources, rationale_md, generated_at, expires_at)"
        " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) RETURNING id",
        (
            agent,
            symbol if direction != "no_action" else None,
            direction,
            None if direction == "no_action" else 4,
            None if direction == "no_action" else size,
            json.dumps(
                [{"title": "T", "publisher": "P", "published_at": "x", "relevance": "primary"}]
            ),
            rationale,
            generated or RUN_START - timedelta(hours=1),
            expires or CLOSE,
        ),
    ).fetchone()
    return str(row["id"])


def add_decision(conn, symbol, at, direction="buy", size="3"):
    conn.execute(
        "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision,"
        " quote_time, generated_at) VALUES (%s, %s, %s, 'private reasoning', 100, %s, %s)",
        (symbol, direction, size, at, at),
    )


def decision(report_ids, symbol="AAPL"):
    return CheckedDecision(
        symbol=symbol,
        direction="buy",
        size_pct=Decimal("4.5"),
        reasoning="because",
        quote=Decimal("200.25"),
        quote_time=RUN_START - timedelta(minutes=1),
        report_ids=tuple(report_ids),
    )


def store(conn):
    return PostgresStore(conn, _allow_savepoints=True)


def read(conn, **kw):
    with as_role(conn, ROLE):
        return store(conn).read_inputs(RUN_START, journal_entries=kw.get("journal_entries", 5))


# --- reads --------------------------------------------------------------------------------


def test_unexpired_reports_from_both_analysts_with_consumed_marked(conn):
    a = add_report(conn, "AAPL", agent="research")
    b = add_report(conn, "MSFT", "sell", agent="opportunistic_identifier", size="0")
    decided = add_report(conn, "NVDA")
    conn.execute(
        "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md,"
        " quote_at_decision, quote_time) VALUES ('NVDA', 'buy', 2, 'r', 100, now())"
    )
    decision_id = conn.execute("SELECT id FROM decisions WHERE symbol = 'NVDA'").fetchone()["id"]
    conn.execute("INSERT INTO decision_reports VALUES (%s, %s)", (decision_id, decided))
    add_report(conn, "TSLA", expires=RUN_START)  # expires_at = run_start: not unexpired
    add_report(conn, "IBM", expires=RUN_START - timedelta(minutes=1))
    add_report(conn, None, "no_action")

    got = {r.symbol: r for r in read(conn).reports}
    assert set(got) == {"AAPL", "MSFT", "NVDA"}
    assert got["AAPL"].id == a and got["AAPL"].agent == "research"
    assert got["MSFT"].id == b and got["MSFT"].agent == "opportunistic_identifier"
    assert got["MSFT"].suggested_size_pct == Decimal(0)
    assert [r.consumed for r in (got["AAPL"], got["MSFT"], got["NVDA"])] == [False, False, True]
    assert got["AAPL"].sources[0]["relevance"] == "primary"
    assert got["AAPL"].rationale_md == "thesis"


def test_todays_latest_snapshot_only(conn):
    ny_midnight = RUN_START.astimezone(NEW_YORK).replace(hour=0, minute=0, second=0)
    for taken, equity in (
        (ny_midnight - timedelta(hours=1), "1"),  # yesterday evening
        (ny_midnight + timedelta(hours=9), "90000"),
        (ny_midnight + timedelta(hours=9, minutes=45), "95000"),  # latest before run_start
        (RUN_START + timedelta(minutes=5), "99999"),  # after run_start
    ):
        conn.execute(
            "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power)"
            " VALUES (%s, %s, 50000, 50000)",
            (taken, equity),
        )
    account = read(conn).account
    assert account.equity == Decimal("95000.00")
    assert account.cash == Decimal("50000.00")


def test_no_snapshot_today_is_none(conn):
    yesterday = RUN_START - timedelta(days=1)
    conn.execute(
        "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power)"
        " VALUES (%s, 1000, 1000, 1000)",
        (yesterday,),
    )
    assert read(conn).account is None


def test_positions_and_the_latest_journal_rows_oldest_first(conn):
    conn.execute("INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('MSFT', 1, 300)")
    conn.execute("INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('AAPL', 10, 150.5)")
    for n in range(1, 5):
        conn.execute(
            "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md,"
            " per_agent_attribution) VALUES (%s, 1000, 1010, %s, '{}'::jsonb)",
            (date(2020, 1, n), f"day {n}"),
        )
    got = read(conn, journal_entries=3)
    assert [p.symbol for p in got.positions] == ["AAPL", "MSFT"]
    assert got.positions[0].qty == Decimal("10.0000")
    assert [j.summary_md for j in got.journal] == ["day 2", "day 3", "day 4"]
    assert read(conn, journal_entries=0).journal == ()


def test_its_own_decisions_today_on_candidate_symbols_only(conn):
    add_report(conn, "AAPL")
    add_report(conn, "MSFT")
    start_of_day = RUN_START - timedelta(hours=1)
    add_decision(conn, "AAPL", start_of_day, "buy", "3")
    add_decision(conn, "AAPL", start_of_day + timedelta(minutes=10), "sell", "1")
    add_decision(conn, "TSLA", start_of_day)  # not a candidate
    add_decision(conn, "MSFT", RUN_START - timedelta(days=1))  # yesterday
    got = read(conn).earlier_decisions
    assert [(d.symbol, d.direction, d.size_pct) for d in got] == [
        ("AAPL", "buy", Decimal("3.000")),
        ("AAPL", "sell", Decimal("1.000")),
    ]
    assert not hasattr(got[0], "reasoning_md")


def test_a_database_failure_is_a_store_error(database_url):
    raw = psycopg.connect(database_url, autocommit=True)
    raw.close()
    with pytest.raises(StoreError):
        PostgresStore(raw).read_inputs(RUN_START, journal_entries=5)
    with pytest.raises(StoreError):
        PostgresStore(raw).write([])


# --- the isolation level, on a real autocommit connection -------------------------------------


class Recorder:
    """A connection proxy that remembers the statements it was given."""

    def __init__(self, conn):
        self._conn = conn
        self.statements: list[str] = []

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, query, *args, **kw):
        self.statements.append(str(query))
        return self._conn.execute(query, *args, **kw)


def test_the_read_is_one_repeatable_read_read_only_transaction(make_database):
    url = make_database()
    apply_migrations(url)
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as raw:
        raw.execute(f"SET ROLE {ROLE}")
        spy = Recorder(raw)
        PostgresStore(spy).read_inputs(RUN_START, journal_entries=5)
        assert spy.statements[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        assert spy.statements[-1] == "ROLLBACK"
        assert raw.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        # and inside it nothing can be written
        raw.execute("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            raw.execute("INSERT INTO decisions (symbol) VALUES ('X')")
        raw.execute("ROLLBACK")


def test_a_non_autocommit_connection_is_refused_outside_tests(database_url):
    with psycopg.connect(database_url) as raw, pytest.raises(StoreError):
        PostgresStore(raw)


# --- the write --------------------------------------------------------------------------------


def test_write_inserts_decisions_and_links_together(conn):
    first, second = (
        add_report(conn, "AAPL"),
        add_report(conn, "AAPL", agent="opportunistic_identifier"),
    )
    other = add_report(conn, "MSFT", "sell", size="0")
    with as_role(conn, ROLE):
        store(conn).write([decision([first, second]), decision([other], "MSFT")])
    rows = conn.execute(
        "SELECT d.symbol, d.size_pct, d.reasoning_md, d.quote_at_decision, d.quote_time,"
        " array_agg(dr.report_id::text ORDER BY dr.report_id::text) AS links"
        " FROM decisions d JOIN decision_reports dr ON dr.decision_id = d.id"
        " GROUP BY d.id ORDER BY d.symbol"
    ).fetchall()
    assert [
        (r["symbol"], r["size_pct"], r["reasoning_md"], r["quote_at_decision"]) for r in rows
    ] == [
        ("AAPL", Decimal("4.500"), "because", Decimal("200.2500")),
        ("MSFT", Decimal("4.500"), "because", Decimal("200.2500")),
    ]
    assert rows[0]["quote_time"] == RUN_START - timedelta(minutes=1)
    assert rows[0]["links"] == sorted([first, second])
    assert rows[1]["links"] == [other]


def test_a_failure_on_a_later_link_leaves_nothing(conn):
    good = add_report(conn, "AAPL")
    missing = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(StoreError), as_role(conn, ROLE):
        store(conn).write([decision([good]), decision([good, missing], "MSFT")])
    assert conn.execute("SELECT count(*) AS n FROM decisions").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM decision_reports").fetchone()["n"] == 0


def test_the_write_is_one_real_transaction_on_an_autocommit_connection(make_database):
    url = make_database()
    apply_migrations(url)
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as raw:
        good = add_report(raw, "AAPL")
        missing = "00000000-0000-0000-0000-000000000000"
        raw.execute(f"SET ROLE {ROLE}")
        with pytest.raises(StoreError):
            PostgresStore(raw).write([decision([good]), decision([good, missing], "MSFT")])
        assert raw.execute("SELECT count(*) AS n FROM decisions").fetchone()["n"] == 0
        assert raw.execute("SELECT count(*) AS n FROM decision_reports").fetchone()["n"] == 0
        PostgresStore(raw).write([decision([good])])
        assert raw.execute("SELECT count(*) AS n FROM decisions").fetchone()["n"] == 1


def test_writing_nothing_is_fine(conn):
    with as_role(conn, ROLE):
        store(conn).write([])
    assert conn.execute("SELECT count(*) AS n FROM decisions").fetchone()["n"] == 0


# --- what the role may not do --------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct,"
        " rationale_md, expires_at)"
        " VALUES ('research', 'AAPL', 'buy', 3, 5, 'x', now() + interval '1 hour')",
        "INSERT INTO risk_verdicts (trading_day, config_version, verdict, rejection_rule)"
        " VALUES (current_date, 'x', 'rejected', 'x')",
        "INSERT INTO orders (id, risk_verdict_id, status)"
        " VALUES ('x', gen_random_uuid(), 'submitted')",
        "SELECT * FROM risk_verdicts",
        "SELECT * FROM orders",
        "UPDATE decisions SET size_pct = 1",
    ],
)
def test_the_role_cannot_touch_anything_but_its_own_rows(conn, statement):
    assert attempt(conn, ROLE, statement) == "denied"
