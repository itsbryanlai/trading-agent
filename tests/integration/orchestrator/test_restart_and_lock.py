"""Restart and single instance on real autocommit connections (FR-021, FR-023, FR-023a).

These commit, so they use a far-off trading day no other test reads, and delete
what they wrote."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.fakes.launcher import FakeLauncher
from tests.unit.orchestrator.support import config
from trading_agent.orchestrator import __main__ as runner
from trading_agent.orchestrator.service import Orchestrator, PgRunStore

DAY = date(2027, 3, 1)
T = datetime(2027, 3, 1, 15, 0, tzinfo=UTC)  # 10:00 EST


def _connect(url, role=True):
    conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
    if role:
        conn.execute("SET ROLE ta_orchestrator")
    return conn


@pytest.fixture
def cleanup(database_url):
    yield
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("DELETE FROM orchestrator_runs WHERE trading_day = %s", (DAY,))


def _insert_running(conn, agent, slot_key, pgid):
    conn.execute(
        "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, slot_at, "
        "started_at, outcome, pgid) VALUES (%s, %s, %s, %s, %s, %s, 'running', %s)",
        (agent, DAY, "event_driven" if slot_key is None else "scheduled", slot_key, T, T, pgid),
    )


def test_startup_stops_orphans_and_marks_every_running_row_interrupted(database_url, cleanup):
    conn = _connect(database_url)
    try:
        _insert_running(conn, "portfolio_manager", None, 4242)
        _insert_running(conn, "research", "research_daily", None)
        launcher = FakeLauncher()
        launcher.live_orphans.add(4242)
        Orchestrator(config(), PgRunStore(conn), launcher, {}.get).startup(T + timedelta(minutes=5))
        assert launcher.group_stops == [(4242, "trading_agent.portfolio_manager")]
        rows = conn.execute(
            "SELECT agent, outcome, detail FROM orchestrator_runs WHERE trading_day = %s", (DAY,)
        ).fetchall()
        assert {(r["agent"], r["outcome"]) for r in rows} == {
            ("portfolio_manager", "interrupted"),
            ("research", "interrupted"),
        }
    finally:
        conn.close()


def test_a_failed_insert_does_not_poison_the_connection(database_url, cleanup):
    conn = _connect(database_url)
    try:
        store = PgRunStore(conn)
        _insert_running(conn, "research", "research_daily", None)
        from trading_agent.orchestrator.planner import Start
        from trading_agent.orchestrator.service import SlotTaken

        with pytest.raises(SlotTaken):
            store.insert_start(Start("research", "catch_up", "research_daily", T), DAY, T)
        run_id = store.insert_start(
            Start("opportunistic_identifier", "scheduled", "oi@10:00", T), DAY, T
        )
        assert run_id is not None
    finally:
        conn.close()


def test_second_orchestrator_is_refused_until_the_first_goes_away(database_url):
    # As ta_orchestrator, the role the real process uses: the lock needs no grant.
    first, second = _connect(database_url), _connect(database_url)
    try:
        runner._take_lock(first, timedelta(0), timedelta(seconds=1), lambda s: None)
        with pytest.raises(runner.AnotherOrchestratorRunning):
            runner._take_lock(second, timedelta(seconds=2), timedelta(seconds=1), lambda s: None)
        first.close()
        # The server releases a closed session's lock asynchronously: allow it a
        # moment rather than racing it (the test flaked under load).
        runner._take_lock(second, timedelta(seconds=5), timedelta(milliseconds=100), time.sleep)
    finally:
        first.close()
        second.close()


def test_the_store_refuses_a_transactional_connection(database_url):
    with psycopg.connect(database_url) as conn:
        with pytest.raises(Exception, match="autocommit"):
            PgRunStore(conn)
