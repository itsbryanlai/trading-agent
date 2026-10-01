"""The service against Postgres as ta_orchestrator, with the fake launcher
(US1, US3, US5; FR-016, FR-023a, FR-024). The test transaction is rolled back."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes.launcher import FakeLauncher
from tests.integration.helpers import as_role, sqlstate_of
from tests.integration.storage.chain import SOURCES
from tests.unit.orchestrator.support import config, et
from trading_agent.orchestrator.launcher import Handle
from trading_agent.orchestrator.service import Orchestrator, PgRunStore

ENV = {"PATH": "/usr/bin"}


@pytest.fixture
def orch(conn):
    return Orchestrator(config(), PgRunStore(conn, _allow_savepoints=True), FakeLauncher(), ENV.get)


def _tick(conn, orch, now):
    with as_role(conn, "ta_orchestrator"):
        return orch.tick(now)


def _rows(conn):
    return conn.execute(
        "SELECT agent, reason, slot_key, outcome, detail, pgid, started_at FROM orchestrator_runs "
        "ORDER BY coalesce(started_at, slot_at), agent"
    ).fetchall()


def _report(conn, generated_at):
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources, "
        "rationale_md, generated_at, expires_at) VALUES ('research', 'AAPL', 'buy', 3, 5, "
        "%s::jsonb, 't', %s, %s + interval '1 day')",
        (SOURCES, generated_at, generated_at),
    )


def test_one_morning(conn, orch):
    _tick(conn, orch, et("08:30"))
    orch.launcher.finish(Handle(5000), 0)
    _tick(conn, orch, et("08:32"))
    _tick(conn, orch, et("10:00"))
    rows = _rows(conn)
    assert [(r["agent"], r["reason"], r["slot_key"], r["outcome"]) for r in rows] == [
        ("research", "scheduled", "research_daily", "succeeded"),
        ("opportunistic_identifier", "scheduled", "oi@10:00", "running"),
        ("portfolio_manager", "morning_session", "morning_session", "running"),
    ]
    assert all(r["pgid"] is not None for r in rows)
    assert rows[0]["detail"] == "exit 0"


def test_the_unique_index_rejects_a_second_claim_on_a_slot(conn, orch):
    _tick(conn, orch, et("08:30"))
    duplicate = (
        "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, slot_at, "
        "started_at, outcome) VALUES ('research', '2026-09-28', 'catch_up', 'research_daily', "
        "%s, %s, 'running')"
    )
    assert sqlstate_of(conn, duplicate, (et("08:30"), et("09:00"))) == "23505"


def test_an_event_driven_run_from_the_report_view(conn, orch):
    _tick(conn, orch, et("08:30"))
    orch.launcher.finish_all(0)
    _tick(conn, orch, et("10:00"))
    orch.launcher.finish_all(0)
    _tick(conn, orch, et("10:05"))
    _report(conn, datetime(2026, 9, 28, 15, 2, tzinfo=UTC))  # 11:02 ET
    _tick(conn, orch, et("11:06"))
    assert not [r for r in _rows(conn) if r["reason"] == "event_driven"]
    _tick(conn, orch, et("11:07"))
    assert [r["outcome"] for r in _rows(conn) if r["reason"] == "event_driven"] == ["running"]


def test_a_paused_morning_is_recorded_and_resuming_runs_the_pm(conn, orch):
    _tick(conn, orch, et("08:30"))
    orch.launcher.finish_all(0)
    _tick(conn, orch, et("08:35"))
    conn.execute("UPDATE system_state SET trading_paused = true")
    _tick(conn, orch, et("10:00"))
    morning = [r for r in _rows(conn) if r["slot_key"] == "morning_session"]
    assert [(r["outcome"], r["detail"]) for r in morning] == [("skipped", "trading paused")]
    _report(conn, datetime(2026, 9, 28, 12, 40, tzinfo=UTC))  # 08:40 ET, never considered
    conn.execute("UPDATE system_state SET trading_paused = false")
    _tick(conn, orch, et("13:00"))
    assert [r["outcome"] for r in _rows(conn) if r["reason"] == "event_driven"] == ["running"]


def test_a_day_with_a_failure_a_timeout_and_a_paused_morning_accounts_for_every_slot(conn, orch):
    _tick(conn, orch, et("08:30"))
    orch.launcher.finish(Handle(5000), 2)  # Research fails
    _tick(conn, orch, et("08:31"))
    conn.execute("UPDATE system_state SET trading_paused = true")
    _tick(conn, orch, et("10:00"))  # morning skipped; Identifier 10:00 starts and hangs
    _tick(conn, orch, et("10:10"))  # Identifier timed out
    rows = _rows(conn)
    summary = [(r["agent"], r["slot_key"], r["outcome"], r["detail"]) for r in rows]
    assert ("research", "research_daily", "failed", "exit 2") in summary
    assert ("portfolio_manager", "morning_session", "skipped", "trading paused") in summary
    assert ("opportunistic_identifier", "oi@10:00", "timed_out", "exit -15") in summary


def test_a_future_dated_report_does_not_block_the_pm(conn, orch):
    # Review H1: one report dated ahead must not hide the real one after it.
    _tick(conn, orch, et("08:30"))
    orch.launcher.finish_all(0)
    _tick(conn, orch, et("10:00"))
    orch.launcher.finish_all(0)
    _tick(conn, orch, et("10:05"))
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources, "
        "rationale_md, generated_at, expires_at) VALUES ('research', 'AAPL', 'buy', 3, 5, "
        "%s::jsonb, 't', now() + interval '3 days', now() + interval '4 days')",
        (SOURCES,),
    )
    _report(conn, datetime(2026, 9, 28, 15, 2, tzinfo=UTC))  # 11:02 ET, a real one
    _tick(conn, orch, et("11:07"))
    assert [r["outcome"] for r in _rows(conn) if r["reason"] == "event_driven"] == ["running"]


def test_a_missing_pause_row_means_unknown_so_the_pm_does_not_run(conn, orch):
    # Review L2: no row must never read as "not paused".
    _tick(conn, orch, et("08:30"))
    orch.launcher.finish_all(0)
    _tick(conn, orch, et("08:35"))
    conn.execute("DELETE FROM system_state")
    with as_role(conn, "ta_orchestrator"):
        assert orch.store.trading_paused() is None
    _tick(conn, orch, et("10:00"))
    morning = [r for r in _rows(conn) if r["slot_key"] == "morning_session"]
    assert [(r["outcome"], r["detail"]) for r in morning] == [("skipped", "pause flag unreadable")]
