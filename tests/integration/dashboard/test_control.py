"""The control writer as a real login in ta_dashboard_control only (specs/014 tasks T019)."""

from __future__ import annotations

import secrets

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from trading_agent.dashboard.control import ControlError, ControlWriter
from trading_agent.storage.migrate import apply_migrations


def _login(admin, group: str) -> tuple[str, str]:
    name, password = f"ta_test_{group}_{secrets.token_hex(4)}_login", secrets.token_hex(8)
    admin.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
            sql.Identifier(name), sql.Literal(password), sql.Identifier(group)
        )
    )
    return name, password


@pytest.fixture(scope="module")
def world(make_database, server_url):
    url = make_database()
    apply_migrations(url)
    admin = psycopg.connect(url, row_factory=dict_row, autocommit=True)
    control_login = _login(admin, "ta_dashboard_control")
    read_login = _login(admin, "ta_dashboard")
    yield {
        "admin": admin,
        "admin_url": url,
        "control_url": make_conninfo(url, user=control_login[0], password=control_login[1]),
        "read_url": make_conninfo(url, user=read_login[0], password=read_login[1]),
        "control_name": control_login[0],
    }
    admin.close()
    with psycopg.connect(server_url, autocommit=True) as server:
        for name in (control_login[0], read_login[0]):
            server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.fixture(autouse=True)
def reset(world):
    yield
    world["admin"].execute("UPDATE system_state SET trading_paused = false")
    world["admin"].execute("UPDATE system_state SET trading_paused_changed_at = NULL")


def _row(world):
    return world["admin"].execute("SELECT * FROM system_state").fetchone()


def _as(url: str, statement: str) -> str | None:
    with psycopg.connect(url, autocommit=True) as connection:
        try:
            connection.execute(statement)
        except psycopg.Error as exc:
            return exc.sqlstate
    return None


def test_a_pause_is_written_and_the_database_stamps_the_time(world):
    writer = ControlWriter(world["control_url"])
    assert writer.set_paused(False, True) == "written"
    row = _row(world)
    assert row["trading_paused"] is True and row["trading_paused_changed_at"] is not None
    assert row["trading_paused_changed_at"] == row["updated_at"] or row["updated_at"] is not None


def test_a_second_identical_call_is_stale_and_changes_nothing(world):
    writer = ControlWriter(world["control_url"])
    assert writer.set_paused(False, True) == "written"
    stamped = _row(world)["trading_paused_changed_at"]
    assert writer.set_paused(False, True) == "stale"
    assert _row(world)["trading_paused_changed_at"] == stamped


def test_a_resume_is_written_and_a_stale_resume_is_not(world):
    writer = ControlWriter(world["control_url"])
    writer.set_paused(False, True)
    assert writer.set_paused(True, False) == "written"
    assert _row(world)["trading_paused"] is False
    assert writer.set_paused(True, False) == "stale"


def test_the_same_update_as_the_read_login_is_denied(world):
    statement = "UPDATE system_state SET trading_paused = true, updated_at = now()"
    assert _as(world["read_url"], statement) == "42501"
    assert _row(world)["trading_paused"] is False


def test_the_control_login_cannot_touch_the_halt_columns_or_the_stamp(world):
    for column, value in (
        ("halt_triggered_on", "current_date"),
        ("daily_starting_equity", "100000"),
        ("baseline_trading_day", "current_date"),
        ("trading_paused_changed_at", "now()"),
    ):
        assert _as(world["control_url"], f"UPDATE system_state SET {column} = {value}") == "42501"


def test_the_control_login_cannot_write_anywhere_else(world):
    for statement in (
        "INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('X', 1, 1)",
        "DELETE FROM system_state",
        "UPDATE orders SET status = 'filled'",
    ):
        assert _as(world["control_url"], statement) == "42501"


def test_a_bad_password_is_a_control_error_without_the_url(world):
    bad = make_conninfo(world["control_url"], password="wrong-" + secrets.token_hex(3))
    with pytest.raises(ControlError) as caught:
        ControlWriter(bad).set_paused(False, True)
    assert str(caught.value) == "database: OperationalError"


def test_a_lock_held_elsewhere_times_out_in_five_seconds_and_changes_nothing(world):
    holder = psycopg.connect(world["admin_url"], row_factory=dict_row)
    try:
        holder.execute("LOCK TABLE system_state IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(ControlError) as caught:
            ControlWriter(world["control_url"]).set_paused(False, True)
        assert str(caught.value) == "database: LockNotAvailable"
    finally:
        holder.rollback()
        holder.close()
    assert _row(world)["trading_paused"] is False


def test_memberships_for_check(world):
    login, roles = ControlWriter(world["control_url"]).memberships()
    assert login["login"] == world["control_name"]
    assert not any(login[k] for k in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolbypassrls"))
    assert roles == ["ta_dashboard_control"]
