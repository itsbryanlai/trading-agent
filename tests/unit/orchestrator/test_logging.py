"""US5: the log lines (contracts/orchestrator-interface.md "Log lines"; FR-008a, FR-027).

Unlike the planner tests, the service really copies values into agents'
environments, so checking that a distinctive value never reaches a log is
meaningful here."""

from __future__ import annotations

import logging

import psycopg
import pytest

from tests.fakes.launcher import FakeLauncher
from tests.unit.orchestrator.support import MemoryStore, config, et
from trading_agent.orchestrator.launcher import Handle
from trading_agent.orchestrator.service import Orchestrator

SECRET = "distinctive-fake-value-7f3a"
ENV = {"PATH": "/usr/bin", "RESEARCH_TOKEN": SECRET, "PORTFOLIO_MANAGER_TOKEN": SECRET}


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG, logger="trading_agent.orchestrator")
    yield caplog
    for record in caplog.records:
        assert SECRET not in record.getMessage()


def make():
    cfg = config(
        research={"env": ("RESEARCH_TOKEN",)},
        portfolio_manager={"env": ("PORTFOLIO_MANAGER_TOKEN",)},
    )
    launcher, store = FakeLauncher(), MemoryStore()
    return Orchestrator(cfg, store, launcher, ENV.get), launcher, store


def messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_start_and_finish_lines(logs):
    orch, launcher, store = make()
    orch.tick(et("08:30"))
    launcher.finish(Handle(5000), 0)
    orch.tick(et("08:31"))
    info = messages(logs, logging.INFO)
    assert "orchestrator: research scheduled started" in info
    assert "orchestrator: research finished: succeeded (exit 0)" in info


def test_failure_skip_and_timeout_lines(logs):
    orch, launcher, store = make()
    orch.tick(et("08:30"))
    launcher.finish(Handle(5000), 3)
    store.paused = True
    orch.tick(et("10:00"))
    orch.tick(et("10:16"))  # the 10:00 Identifier has hung past its 15 minutes
    warnings = messages(logs, logging.WARNING)
    assert "orchestrator: research finished: failed (exit 3)" in warnings
    assert "orchestrator: portfolio_manager morning_session skipped: trading paused" in warnings
    assert "orchestrator: opportunistic_identifier timed_out: exit -15" in warnings


def test_due_but_paused_is_logged_once_per_pause_episode(logs):
    orch, launcher, store = make()
    store.paused = True
    orch.tick(et("08:30"))
    launcher.finish(Handle(5000), 0)
    orch.tick(et("10:00"))  # the morning session is skipped
    store.latest_report = et("08:35")
    for minute in range(1, 6):
        orch.tick(et(f"11:0{minute}"))
    held = [m for m in messages(logs, logging.WARNING) if "due but" in m]
    assert held == ["orchestrator: portfolio_manager due but trading paused"]
    store.paused = False
    orch.tick(et("11:10"))
    assert any("portfolio_manager event_driven started" in m for m in messages(logs, logging.INFO))


def test_unreadable_state_is_an_error_line(logs):
    orch, launcher, store = make()
    store.read_error["paused"] = psycopg.errors.InsufficientPrivilege("denied")
    store.read_error["latest"] = psycopg.errors.UndefinedTable("gone")
    orch.tick(et("08:30"))
    errors = messages(logs, logging.ERROR)
    assert "orchestrator: cannot read the pause flag: InsufficientPrivilege" in errors
    assert "orchestrator: cannot read the latest report time: UndefinedTable" in errors


def test_a_lost_connection_is_not_swallowed():
    orch, launcher, store = make()
    store.read_error["paused"] = psycopg.OperationalError("gone")
    with pytest.raises(psycopg.OperationalError):
        orch.tick(et("08:30"))
