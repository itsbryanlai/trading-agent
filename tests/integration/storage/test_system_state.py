"""US4: a manual pause, and a daily-loss halt that clears itself."""

from __future__ import annotations

import pytest

from tests.integration.helpers import as_role, attempt, sqlstate_of

CHECK_VIOLATION = "23514"


def _effective(conn):
    return conn.execute("SELECT * FROM system_state_effective").fetchone()


def _row_version(conn):
    return conn.execute("SELECT xmin::text AS v FROM system_state").fetchone()["v"]


def test_fresh_database_has_one_row_not_paused_and_no_halt(conn):
    rows = conn.execute("SELECT * FROM system_state").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["trading_paused"] is False
    assert row["halt_triggered_on"] is None
    assert row["baseline_trading_day"] is None
    assert row["daily_starting_equity"] is None


def test_second_row_impossible(conn):
    assert sqlstate_of(conn, "INSERT INTO system_state DEFAULT VALUES") is not None
    assert sqlstate_of(conn, "INSERT INTO system_state (id) VALUES (false)") == CHECK_VIOLATION


def test_pause_toggle_readable_until_toggled_back(conn):
    with as_role(conn, "ta_dashboard_control"):
        conn.execute("UPDATE system_state SET trading_paused = true, updated_at = now()")
    # 005 narrowed the orchestrator to the flag itself (research O7).
    with as_role(conn, "ta_orchestrator"):
        row = conn.execute("SELECT trading_paused FROM system_state").fetchone()
        assert row["trading_paused"] is True
    with as_role(conn, "ta_dashboard"):
        assert _effective(conn)["trading_paused"] is True


@pytest.mark.parametrize(
    ("role", "column", "value"),
    [
        ("ta_dashboard_control", "halt_triggered_on", "current_date"),
        ("ta_dashboard_control", "daily_starting_equity", "100000"),
        ("ta_risk_gate", "trading_paused", "true"),
    ],
)
def test_each_writer_confined_to_its_own_columns(conn, role, column, value):
    statement = f"UPDATE system_state SET {column} = {value}"
    assert attempt(conn, role, statement) == "denied"


def test_halt_recorded_today_is_active(conn):
    with as_role(conn, "ta_risk_gate"):
        conn.execute(
            "UPDATE system_state SET halt_triggered_on = "
            "(SELECT current_trading_date FROM system_state_effective), updated_at = now()"
        )
    assert _effective(conn)["daily_loss_halt_active"] is True


def test_yesterdays_halt_reads_inactive_with_no_write(conn):
    conn.execute(
        "UPDATE system_state SET halt_triggered_on = "
        "(SELECT current_trading_date - 1 FROM system_state_effective)"
    )
    version = _row_version(conn)
    first = _effective(conn)
    second = _effective(conn)
    assert first["daily_loss_halt_active"] is False
    assert second["daily_loss_halt_active"] is False
    assert _row_version(conn) == version, "reading the halt must not write system_state"


def test_baseline_only_visible_on_its_own_trading_day(conn):
    conn.execute(
        "UPDATE system_state SET daily_starting_equity = 100000, baseline_trading_day = "
        "(SELECT current_trading_date - 1 FROM system_state_effective)"
    )
    assert _effective(conn)["daily_starting_equity"] is None

    conn.execute(
        "UPDATE system_state SET baseline_trading_day = "
        "(SELECT current_trading_date FROM system_state_effective)"
    )
    assert _effective(conn)["daily_starting_equity"] == 100000


def test_current_trading_date_is_new_york_date(conn):
    row = conn.execute(
        "SELECT current_trading_date = (now() AT TIME ZONE 'America/New_York')::date AS same "
        "FROM system_state_effective"
    ).fetchone()
    assert row["same"] is True


def test_non_positive_baseline_rejected(conn):
    assert sqlstate_of(conn, "UPDATE system_state SET daily_starting_equity = 0") == (
        CHECK_VIOLATION
    )


# --- Migration 0015 (feature 014, ADR 0027): when the pause last changed ---------------------

_CHANGED_AT = "SELECT trading_paused_changed_at AS at, now() AS now FROM system_state"
_LONG_AGO = "2020-01-01 00:00:00+00"


def _set_pause_as_owner(conn, paused: bool, changed_at: str | None):
    """Put the row in a known state. The connection's role is a superuser and sets the stamp
    in a second statement, since an update that flips the flag has the trigger stamp it."""
    conn.execute("UPDATE system_state SET trading_paused = %s", (paused,))
    conn.execute("UPDATE system_state SET trading_paused_changed_at = %s", (changed_at,))


def test_the_column_is_null_on_a_fresh_database(conn):
    assert conn.execute(_CHANGED_AT).fetchone()["at"] is None


@pytest.mark.parametrize(("before", "after"), [(False, True), (True, False)])
def test_a_change_of_the_flag_by_the_control_login_stamps_the_transaction_time(conn, before, after):
    _set_pause_as_owner(conn, before, _LONG_AGO)
    with as_role(conn, "ta_dashboard_control"):
        conn.execute("UPDATE system_state SET trading_paused = %s, updated_at = now()", (after,))
    row = conn.execute(_CHANGED_AT).fetchone()
    assert row["at"] == row["now"]


def test_an_update_that_leaves_the_flag_alone_leaves_the_column_alone(conn):
    _set_pause_as_owner(conn, True, _LONG_AGO)
    with as_role(conn, "ta_dashboard_control"):
        conn.execute("UPDATE system_state SET trading_paused = true, updated_at = now()")
    row = conn.execute(_CHANGED_AT).fetchone()
    assert row["at"].isoformat().startswith("2020-01-01")


def test_the_risk_gates_baseline_and_halt_updates_succeed_and_leave_the_column_alone(conn):
    _set_pause_as_owner(conn, True, _LONG_AGO)
    with as_role(conn, "ta_risk_gate"):
        conn.execute(
            "UPDATE system_state SET baseline_trading_day = current_date, "
            "daily_starting_equity = 100000, updated_at = now()"
        )
        conn.execute("UPDATE system_state SET halt_triggered_on = current_date, updated_at = now()")
    row = conn.execute(_CHANGED_AT).fetchone()
    assert row["at"].isoformat().startswith("2020-01-01")


@pytest.mark.parametrize(
    "role", ["ta_dashboard_control", "ta_risk_gate", "ta_dashboard", "ta_assistant"]
)
def test_no_role_may_set_the_column_directly(conn, role):
    statement = "UPDATE system_state SET trading_paused_changed_at = now()"
    assert attempt(conn, role, statement) == "denied"


def test_the_orchestrator_cannot_read_the_column_but_the_dashboard_and_assistant_can(conn):
    select = "SELECT trading_paused_changed_at FROM system_state"
    assert attempt(conn, "ta_orchestrator", select) == "denied"
    assert attempt(conn, "ta_dashboard", select) == "allowed"
    assert attempt(conn, "ta_assistant", select) == "allowed"
