"""The dashboard store's state queries as the read role (specs/014 tasks T013).

The store connects as a login created here, a member of ta_dashboard only. The data is
committed to a database of this module's own.
"""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from trading_agent.dashboard.store import NO_LOWER_BOUND, PostgresStore, StoreError
from trading_agent.storage.migrate import apply_migrations

T = datetime


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
    yield {
        "admin": admin,
        "login_url": make_conninfo(url, user=login, password=password),
    }
    admin.close()
    with psycopg.connect(server_url, autocommit=True) as server:
        server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login)))


@pytest.fixture
def store(world) -> PostgresStore:
    return PostgresStore(world["login_url"], 10)


@pytest.fixture(autouse=True)
def clean(world):
    yield
    admin = world["admin"]
    admin.execute("DELETE FROM positions")
    admin.execute("DELETE FROM account_snapshots")
    admin.execute("DELETE FROM orchestrator_runs")
    admin.execute("UPDATE system_state SET trading_paused = false, halt_triggered_on = NULL")
    admin.execute("UPDATE system_state SET trading_paused_changed_at = NULL")


def _snapshot(admin, taken_at: datetime, equity: str) -> None:
    admin.execute(
        "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
        "VALUES (%s, %s, 1000, 2000)",
        (taken_at, equity),
    )


def _pm_run(admin, day: str, slot_key: str, outcome: str, detail, *, at: datetime, started=True):
    admin.execute(
        "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, slot_at, "
        "started_at, finished_at, outcome, detail) "
        "VALUES ('portfolio_manager', %s, 'morning_session', %s, %s, %s, %s, %s, %s)",
        (
            day,
            slot_key,
            at,
            at if started else None,
            at + timedelta(minutes=2) if outcome not in ("running", "skipped") else None,
            outcome,
            detail,
        ),
    )


def test_state_on_a_fresh_database(store):
    with store.read() as reader:
        state = reader.state()
    assert state["trading_paused"] is False
    assert state["trading_paused_changed_at"] is None
    assert state["daily_loss_halt_active"] is False
    assert isinstance(state["current_trading_date"], date)


def test_state_after_a_pause_carries_the_trigger_stamp_and_the_halt(world, store):
    admin = world["admin"]
    admin.execute(
        "UPDATE system_state SET trading_paused = true, halt_triggered_on = %s",
        (T.now(UTC).astimezone().date(),),
    )
    with store.read() as reader:
        state = reader.state()
    assert state["trading_paused"] is True
    assert isinstance(state["trading_paused_changed_at"], datetime)
    assert state["trading_paused_changed_at"].tzinfo is not None
    assert abs(T.now(UTC) - state["trading_paused_changed_at"]) < timedelta(minutes=1)
    assert isinstance(state["daily_loss_halt_active"], bool)


def test_positions_are_ordered_by_symbol_with_exact_types(world, store):
    admin = world["admin"]
    admin.execute(
        "INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('NVDA', 3, 100.5), "
        "('AAPL', 10, 187.25)"
    )
    with store.read() as reader:
        rows = reader.positions()
    assert [r["symbol"] for r in rows] == ["AAPL", "NVDA"]
    assert rows[0]["qty"] == Decimal("10") and rows[0]["avg_entry_price"] == Decimal("187.25")
    assert isinstance(rows[0]["updated_at"], datetime)


def test_latest_snapshot_is_the_newest_or_none(world, store):
    with store.read() as reader:
        assert reader.latest_snapshot() is None
    admin = world["admin"]
    _snapshot(admin, T(2026, 10, 19, 14, 0, tzinfo=UTC), "100.00")
    _snapshot(admin, T(2026, 10, 19, 20, 0, tzinfo=UTC), "110.50")
    with store.read() as reader:
        snapshot = reader.latest_snapshot()
    assert snapshot["taken_at"] == T(2026, 10, 19, 20, 0, tzinfo=UTC)
    assert snapshot["equity"] == Decimal("110.50")
    assert snapshot["cash"] == Decimal("1000.00") and snapshot["buying_power"] == Decimal("2000.00")


def _seed_two_days(admin) -> None:
    _snapshot(admin, T(2026, 10, 19, 14, 0, tzinfo=UTC), "100.00")  # Mon 10:00 ET
    _snapshot(admin, T(2026, 10, 19, 20, 0, tzinfo=UTC), "110.00")  # Mon 16:00 ET (last of Mon)
    _snapshot(admin, T(2026, 10, 20, 15, 0, tzinfo=UTC), "120.00")  # Tue 11:00 ET
    _snapshot(admin, T(2026, 10, 20, 19, 0, tzinfo=UTC), "130.00")  # Tue 15:00 ET
    _snapshot(admin, T(2026, 10, 21, 1, 0, tzinfo=UTC), "140.00")  # Tue 21:00 ET: still Tuesday


def test_every_point_since_a_time_is_returned_oldest_first(world, store):
    _seed_two_days(world["admin"])
    since = T(2026, 10, 20, 0, 0, tzinfo=UTC)
    with store.read() as reader:
        rows = reader.equity_points(since, daily=False)
    assert [r["equity"] for r in rows] == [Decimal("120.00"), Decimal("130.00"), Decimal("140.00")]
    assert set(rows[0]) == {"taken_at", "equity"}


def test_no_lower_bound_returns_everything(world, store):
    _seed_two_days(world["admin"])
    with store.read() as reader:
        assert len(reader.equity_points(None, daily=False)) == 5
        assert len(reader.equity_points(None, daily=True)) == 2
    assert NO_LOWER_BOUND.tzinfo is not None


def test_daily_returns_the_last_snapshot_of_each_new_york_day(world, store):
    _seed_two_days(world["admin"])
    with store.read() as reader:
        rows = reader.equity_points(T(2026, 10, 1, tzinfo=UTC), daily=True)
    assert [r["equity"] for r in rows] == [Decimal("110.00"), Decimal("140.00")]
    assert rows[0]["taken_at"] == T(2026, 10, 19, 20, 0, tzinfo=UTC)
    assert rows[1]["taken_at"] == T(2026, 10, 21, 1, 0, tzinfo=UTC)  # grouped by ET date


def test_daily_respects_the_lower_bound(world, store):
    _seed_two_days(world["admin"])
    with store.read() as reader:
        rows = reader.equity_points(T(2026, 10, 20, 0, 0, tzinfo=UTC), daily=True)
    assert [r["equity"] for r in rows] == [Decimal("140.00")]


def test_an_insert_inside_read_fails_because_the_transaction_is_read_only(world, store):
    with pytest.raises(StoreError) as caught:
        with store.read() as reader:
            reader._connection.execute("UPDATE system_state SET trading_paused = true")
    assert str(caught.value) == "database: ReadOnlySqlTransaction"
    assert caught.value.__cause__ is None
    with store.read() as reader:  # nothing changed
        assert reader.state()["trading_paused"] is False


def test_a_wrong_password_is_a_store_error_without_the_url(world):
    bad = make_conninfo(world["login_url"], password="wrong-" + secrets.token_hex(4))
    with pytest.raises(StoreError) as caught:
        with PostgresStore(bad, 10).read():
            pass
    assert str(caught.value) == "database: OperationalError"


def test_the_statement_timeout_applies_to_the_transaction(world):
    store = PostgresStore(world["login_url"], 1)
    with store.read() as reader:
        shown = reader._connection.execute("SHOW statement_timeout").fetchone()
    assert shown["statement_timeout"] == "1s"


def test_latest_pm_run_is_none_without_runs(store):
    with store.read() as reader:
        assert reader.latest_pm_run() is None


def test_latest_pm_run_is_the_newest_by_start_or_slot_time(world, store):
    admin = world["admin"]
    day1, day2, day3 = (
        T(2026, 10, 15, 14, 0, tzinfo=UTC),
        T(2026, 10, 16, 14, 0, tzinfo=UTC),
        T(2026, 10, 19, 14, 0, tzinfo=UTC),
    )
    _pm_run(admin, "2026-10-15", "morning_session", "succeeded", None, at=day1)
    _pm_run(
        admin, "2026-10-19", "morning_session", "skipped", "trading paused", at=day3, started=False
    )
    _pm_run(admin, "2026-10-16", "morning_session", "failed", "exit 1", at=day2)
    # An analyst's newer row is not the Portfolio Manager's.
    admin.execute(
        "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, slot_at, outcome, "
        "detail) VALUES ('research', '2026-10-20', 'scheduled', 'research_daily', %s, "
        "'skipped', 'x')",
        (T(2026, 10, 20, 12, 0, tzinfo=UTC),),
    )
    with store.read() as reader:
        run = reader.latest_pm_run()
    assert run["outcome"] == "skipped" and run["detail"] == "trading paused"
    assert run["slot_key"] == "morning_session"
    assert run["at"] == day3  # a skipped row is ordered by its slot time


def test_a_started_run_is_ordered_by_its_start_not_its_slot(world, store):
    admin = world["admin"]
    admin.execute(
        "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, slot_at, "
        "started_at, outcome) VALUES ('portfolio_manager', '2026-10-19', 'morning_session', "
        "'morning_session', %s, %s, 'running')",
        (T(2026, 10, 19, 14, 0, tzinfo=UTC), T(2026, 10, 19, 14, 30, tzinfo=UTC)),
    )
    _pm_run(
        admin, "2026-10-19", "late", "skipped", "trading paused",
        at=T(2026, 10, 19, 14, 10, tzinfo=UTC), started=False,
    )  # fmt: skip
    with store.read() as reader:
        run = reader.latest_pm_run()
    assert run["outcome"] == "running" and run["at"] == T(2026, 10, 19, 14, 30, tzinfo=UTC)


def test_ping(store):
    with store.read() as reader:
        assert reader.ping() is True
