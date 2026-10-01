"""Migration 0010 (specs/005-orchestrator research O5-O7): the orchestrator's run
records, its one-value view of report times, and its read narrowed to the pause flag."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tests.integration.helpers import as_role, attempt, sqlstate_of
from tests.integration.storage.chain import SOURCES

DAY = date(2026, 9, 28)
T = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"


def _report(conn, generated_at):
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources, "
        "rationale_md, generated_at, expires_at) VALUES ('research', 'AAPL', 'buy', 3, 5, "
        "%s::jsonb, 't', %s, %s + interval '1 day')",
        (SOURCES, generated_at, generated_at),
    )


def _run(conn, **fields):
    row = {
        "agent": "research",
        "trading_day": DAY,
        "reason": "scheduled",
        "slot_key": "research_daily",
        "slot_at": T,
        "started_at": T,
        "finished_at": None,
        "outcome": "running",
        "detail": None,
    }
    row.update(fields)
    columns = ", ".join(row)
    placeholders = ", ".join(["%s"] * len(row))
    return f"INSERT INTO orchestrator_runs ({columns}) VALUES ({placeholders})", tuple(row.values())


def test_latest_report_time_is_one_value(conn):
    with as_role(conn, "ta_orchestrator"):
        cur = conn.execute("SELECT * FROM latest_report_time")
        assert [c.name for c in cur.description] == ["generated_at"]
        assert cur.fetchall() == [{"generated_at": None}]
    _report(conn, datetime(2026, 9, 25, 15, tzinfo=UTC))
    _report(conn, datetime(2026, 9, 28, 13, tzinfo=UTC))
    with as_role(conn, "ta_orchestrator"):
        row = conn.execute("SELECT generated_at FROM latest_report_time").fetchone()
    assert row["generated_at"] == datetime(2026, 9, 28, 13, tzinfo=UTC)


def test_orchestrator_reads_only_the_pause_flag(conn):
    assert attempt(conn, "ta_orchestrator", "SELECT trading_paused FROM system_state") == "allowed"
    for statement in (
        "SELECT daily_starting_equity FROM system_state",
        "SELECT * FROM system_state",
        "SELECT * FROM system_state_effective",
        "SELECT symbol FROM reports WHERE false",
        "SELECT symbol FROM decisions WHERE false",
        "SELECT symbol FROM positions WHERE false",
    ):
        assert attempt(conn, "ta_orchestrator", statement) == "denied", statement


def test_orchestrator_writes_only_its_own_columns(conn):
    statement, params = _run(conn)
    with as_role(conn, "ta_orchestrator"):
        run_id = conn.execute(statement + " RETURNING id", params).fetchone()["id"]
        conn.execute(
            "UPDATE orchestrator_runs SET pgid = 4242, outcome = 'succeeded', finished_at = %s, "
            "detail = 'exit 0' WHERE id = %s",
            (T, run_id),
        )
    assert (
        attempt(conn, "ta_orchestrator", "UPDATE orchestrator_runs SET agent = 'research'")
        == "denied"
    )
    assert attempt(conn, "ta_orchestrator", "DELETE FROM orchestrator_runs") == "denied"


@pytest.mark.parametrize(
    "fields",
    [
        {"agent": "execution"},
        {"reason": "manual"},
        {"outcome": "done"},
        {"outcome": "skipped"},  # skipped but started_at set
        {"outcome": "running", "finished_at": T},  # running but finished
        {"outcome": "succeeded", "started_at": None, "finished_at": T},  # not skipped, no start
        {"reason": "event_driven"},  # event-driven with a slot key
        {"slot_key": None},  # scheduled without a slot key
    ],
)
def test_check_constraints(conn, fields):
    statement, params = _run(conn, **fields)
    assert sqlstate_of(conn, statement, params) == CHECK_VIOLATION


def test_a_skipped_row_has_no_start(conn):
    statement, params = _run(conn, outcome="skipped", started_at=None, detail="trading paused")
    assert sqlstate_of(conn, statement, params) is None


def test_one_row_per_slot_whatever_the_reason(conn):
    first, params = _run(
        conn, agent="portfolio_manager", reason="morning_session", slot_key="morning_session"
    )
    conn.execute(first, params)
    again, params = _run(
        conn, agent="portfolio_manager", reason="catch_up", slot_key="morning_session"
    )
    assert sqlstate_of(conn, again, params) == UNIQUE_VIOLATION


def test_event_driven_runs_are_not_slot_constrained(conn):
    for _ in range(2):
        statement, params = _run(
            conn, agent="portfolio_manager", reason="event_driven", slot_key=None, slot_at=None
        )
        conn.execute(statement, params)
    count = conn.execute(
        "SELECT count(*) AS n FROM orchestrator_runs WHERE reason = 'event_driven'"
    ).fetchone()["n"]
    assert count == 2


def test_reports_policies_are_unchanged(conn):
    rows = conn.execute(
        "SELECT policyname FROM pg_policies WHERE tablename = 'reports' ORDER BY policyname"
    ).fetchall()
    assert [r["policyname"] for r in rows] == [
        "reports_insert_opportunistic_identifier",
        "reports_insert_research",
        "reports_select",
    ]


def test_assistant_and_dashboard_can_read_the_records(conn):
    for role in ("ta_assistant", "ta_dashboard"):
        assert attempt(conn, role, "SELECT * FROM orchestrator_runs WHERE false") == "allowed"
        assert attempt(conn, role, "SELECT * FROM latest_report_time") == "allowed"


def test_a_future_dated_report_is_ignored(conn):
    # One report dated ahead must not hide the real ones after it (review H1).
    real = datetime(2026, 9, 28, 13, tzinfo=UTC)
    _report(conn, real)
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources, "
        "rationale_md, generated_at, expires_at) VALUES ('research', 'AAPL', 'buy', 3, 5, "
        "%s::jsonb, 't', now() + interval '3 days', now() + interval '4 days')",
        (SOURCES,),
    )
    with as_role(conn, "ta_orchestrator"):
        row = conn.execute("SELECT generated_at FROM latest_report_time").fetchone()
    assert row["generated_at"] == real


def test_a_finished_run_cannot_be_changed(conn):
    statement, params = _run(conn)
    with as_role(conn, "ta_orchestrator"):
        run_id = conn.execute(statement + " RETURNING id", params).fetchone()["id"]
        conn.execute("UPDATE orchestrator_runs SET pgid = 7 WHERE id = %s", (run_id,))
        conn.execute(
            "UPDATE orchestrator_runs SET outcome = 'failed', finished_at = %s WHERE id = %s",
            (T, run_id),
        )
    for change in ("outcome = 'succeeded'", "detail = 'rewritten'", "pgid = 9"):
        assert (
            sqlstate_of(conn, f"UPDATE orchestrator_runs SET {change} WHERE id = %s", (run_id,))
            == CHECK_VIOLATION
        ), change
