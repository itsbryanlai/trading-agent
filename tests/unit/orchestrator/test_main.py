"""The process: startup, exit codes, shutdown and its environment reads
(research O12; FR-005a, FR-008a, FR-021, FR-022). Everything is stubbed."""

from __future__ import annotations

import os
from datetime import timedelta

import psycopg
import pytest
import yaml

from tests.fakes.launcher import FakeLauncher
from tests.unit.orchestrator.support import MemoryStore, et
from trading_agent.orchestrator import __main__ as runner
from trading_agent.orchestrator.config import DEFAULT_CONFIG_PATH

FAKE_URL = "postgresql://localhost/fake"
NOW = et("08:30")


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, lock_free=True):
        self.autocommit = True
        self.closed = False
        self.statements = []
        self.lock_free = lock_free

    def execute(self, statement, params=None):
        self.statements.append(statement)
        if "pg_try_advisory_lock" in statement:
            assert params == (runner.SINGLE_INSTANCE_LOCK,)
            return Result({"mine": self.lock_free})
        return Result(None)

    def close(self):
        self.closed = True


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(runner.DATABASE_VARIABLE, FAKE_URL)


def _enabled_config(tmp_path, **env_names):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    for agent in data:
        data[agent]["enabled"] = True
        data[agent]["env"] = env_names.get(agent, [])
    path = tmp_path / "schedule.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def run(conn=None, connect_error=None, store=None, launcher=None, sleep=None, **kwargs):
    conn = conn or FakeConn()
    store = store if store is not None else MemoryStore()
    launcher = launcher or FakeLauncher()
    seen = {}

    def connect(url, **options):
        seen["url"], seen["options"] = url, options
        if connect_error:
            raise connect_error
        return conn

    kwargs.setdefault("clock", lambda: NOW)
    code = runner.main(
        connect=connect,
        launcher_factory=lambda: launcher,
        store_factory=lambda c: store,
        sleep=sleep or (lambda s: None),
        monotonic=lambda: 0.0,
        lock_wait=timedelta(seconds=30),
        lock_retry=timedelta(seconds=15),
        install_signal_handlers=False,
        **kwargs,
    )
    return code, seen, conn, store, launcher


def test_missing_database_variable_refuses_by_name(monkeypatch, caplog):
    monkeypatch.delenv(runner.DATABASE_VARIABLE, raising=False)
    code, seen, *_ = run(max_ticks=1)
    assert code == runner.EXIT_REFUSED and "url" not in seen
    assert runner.DATABASE_VARIABLE in caplog.text


def test_bad_config_refuses(env, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("research: {}\n")
    code, seen, *_ = run(max_ticks=1, config_path=bad)
    assert code == runner.EXIT_REFUSED and "url" not in seen


def test_the_prefix_rule_refuses_to_start(env, tmp_path):
    path = _enabled_config(tmp_path, research=["EXECUTION_DATABASE_URL"])
    code, seen, *_ = run(max_ticks=1, config_path=path)
    assert code == runner.EXIT_REFUSED


def test_database_unreachable_exits_3(env):
    code, *_ = run(connect_error=psycopg.OperationalError("no"), max_ticks=1)
    assert code == runner.EXIT_DATABASE_LOST


def test_lock_held_elsewhere_refuses_after_waiting(env):
    code, _, conn, *_ = run(conn=FakeConn(lock_free=False), max_ticks=1)
    assert code == runner.EXIT_REFUSED and conn.closed
    assert sum("pg_try_advisory_lock" in s for s in conn.statements) == 3


def test_clean_run_reaps_first_then_ticks(env, tmp_path):
    store = MemoryStore()
    from trading_agent.orchestrator.planner import RunRecord

    store.records[1] = RunRecord(
        1,
        "research",
        et("08:30").date(),
        "scheduled",
        "research_daily",
        et("08:30"),
        et("08:30"),
        None,
        "running",
        pgid=777,
    )
    launcher = FakeLauncher()
    code, seen, conn, store, launcher = run(
        store=store,
        launcher=launcher,
        max_ticks=2,
        clock=lambda: et("09:00"),
        config_path=_enabled_config(tmp_path),
    )
    assert code == runner.EXIT_OK and conn.closed
    assert seen["url"] == FAKE_URL and seen["options"]["autocommit"] is True
    assert launcher.group_stops == [(777, "trading_agent.research")]
    assert store.records[1].outcome == "interrupted"
    assert any("tcp_keepalives_idle" in s for s in conn.statements)


def test_shutdown_stops_running_agents_when_the_loop_ends(env, tmp_path):
    code, _, _, store, launcher = run(
        max_ticks=1, clock=lambda: et("10:00"), config_path=_enabled_config(tmp_path)
    )
    assert code == runner.EXIT_OK
    assert launcher.starts and len(launcher.stops) == len(launcher.starts)
    assert all(r.outcome != "running" for r in store.records.values())


def test_a_signal_mid_loop_stops_agents_and_exits_cleanly(env, tmp_path):
    def sleep(seconds):
        raise runner.Stopping("SIGTERM")

    code, _, _, store, launcher = run(
        sleep=sleep, max_ticks=5, clock=lambda: et("10:00"), config_path=_enabled_config(tmp_path)
    )
    assert code == runner.EXIT_OK
    assert launcher.starts and len(launcher.stops) == len(launcher.starts)


def test_a_lost_connection_mid_loop_exits_3_after_stopping_agents(env, tmp_path):
    store = MemoryStore()
    calls = {"n": 0}
    original = store.trading_paused

    def paused():
        calls["n"] += 1
        if calls["n"] > 1:
            raise psycopg.OperationalError("gone")
        return original()

    store.trading_paused = paused
    code, _, conn, store, launcher = run(
        store=store, max_ticks=3, clock=lambda: et("10:00"), config_path=_enabled_config(tmp_path)
    )
    assert code == runner.EXIT_DATABASE_LOST and conn.closed
    assert launcher.starts and len(launcher.stops) == len(launcher.starts)


def test_reads_only_its_own_variable_and_enabled_agents_names(env, monkeypatch, tmp_path):
    read = []

    class Recording(dict):
        def get(self, name, default=None):
            read.append(name)
            return super().get(name, default)

        def __getitem__(self, name):
            read.append(name)
            return super().__getitem__(name)

    monkeypatch.setattr(os, "environ", Recording(os.environ))
    path = _enabled_config(tmp_path, research=["RESEARCH_TOKEN"])
    run(max_ticks=1, clock=lambda: et("08:30"), config_path=path)
    from trading_agent.orchestrator.service import BASE_ENVIRONMENT

    assert set(read) <= {runner.DATABASE_VARIABLE, "RESEARCH_TOKEN", *BASE_ENVIRONMENT}
    assert "RESEARCH_TOKEN" in read  # looked up when Research started
