"""The process: startup checks, exit codes and its environment (research D13;
contracts/reference-data-interface.md). Everything is stubbed; no network, no DB."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from tests.fakes.market_data import FakeMarketData
from tests.unit.reference.support import MemoryStore
from trading_agent.reference import __main__ as runner
from trading_agent.reference.provider import KeyRejected, ProviderUnavailable, RateLimited
from trading_agent.reference.service import ReferenceJob
from trading_agent.reference.symbols import Candidate

NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)
FAKE_KEY = "test-key-not-real"
FAKE_URL = "postgresql://localhost/fake"


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, lock_free=True, close_after=None):
        self.autocommit = True
        self.closed = False
        self.statements = []
        self.lock_free = lock_free
        self.close_after = close_after

    def execute(self, statement, params=None):
        self.statements.append(statement)
        if "pg_try_advisory_lock" in statement:
            assert params == (runner.SINGLE_INSTANCE_LOCK,)
            return Result({"mine": self.lock_free})
        return Result(None)

    def close(self):
        self.closed = True


class FakeJob:
    def __init__(self, provider, store, config, *, sleep, monotonic, symbol_list):
        self.symbol_list = symbol_list
        self.ticks = []
        self.error = None
        FakeJob.last = self

    def tick(self, now):
        self.ticks.append(now)
        if self.error is not None:
            raise self.error


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(runner.KEY_VARIABLE, FAKE_KEY)
    monkeypatch.setenv(runner.DATABASE_VARIABLE, FAKE_URL)


def run(fake=None, conn=None, connect_error=None, job_factory=FakeJob, **kwargs):
    fake = fake or FakeMarketData()
    conn = conn or FakeConn()
    seen = {}

    def connect(url, **options):
        seen["url"], seen["options"] = url, options
        if connect_error:
            raise connect_error
        return conn

    code = runner.main(
        [],
        provider_factory=lambda key: (seen.setdefault("key", key), fake)[1],
        connect=connect,
        job_factory=job_factory,
        store_factory=lambda c: ("store", c),
        sleep=lambda s: None,
        monotonic=lambda: 0.0,
        clock=lambda: NOW,
        lock_retry=timedelta(seconds=15),
        lock_wait=timedelta(seconds=30),
        **kwargs,
    )
    return code, seen, conn


@pytest.mark.parametrize("missing", [runner.KEY_VARIABLE, runner.DATABASE_VARIABLE])
def test_missing_variable_refuses_by_name(env, monkeypatch, caplog, missing):
    monkeypatch.delenv(missing)
    code, seen, _ = run(max_ticks=1)
    assert code == runner.EXIT_REFUSED and "key" not in seen
    assert missing in caplog.text and FAKE_KEY not in caplog.text and FAKE_URL not in caplog.text


def test_bad_config_refuses(env, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("seed_symbols: []\n")
    code, seen, _ = run(max_ticks=1, config_path=bad)
    assert code == runner.EXIT_REFUSED and "url" not in seen


@pytest.mark.parametrize(
    "error", [KeyRejected("401"), ProviderUnavailable("down"), RateLimited("429")]
)
def test_key_check_failure_refuses_before_the_database(env, error):
    fake = FakeMarketData()
    fake.fail("list_us_symbols", error=error)
    code, seen, _ = run(fake=fake, max_ticks=1)
    assert code == runner.EXIT_REFUSED and "url" not in seen


def test_database_unreachable_exits_3(env):
    code, _, _ = run(connect_error=psycopg.OperationalError("no"), max_ticks=1)
    assert code == runner.EXIT_DATABASE_LOST


def test_lock_held_elsewhere_refuses_after_waiting(env):
    conn = FakeConn(lock_free=False)
    code, _, conn = run(conn=conn, max_ticks=1)
    assert code == runner.EXIT_REFUSED and conn.closed
    assert sum("pg_try_advisory_lock" in s for s in conn.statements) == 3


def test_clean_run_hands_the_startup_list_to_the_job_tagged_with_its_day(env):
    fake = FakeMarketData()
    fake.add("AAPL")
    code, seen, conn = run(fake=fake, max_ticks=2)
    assert code == runner.EXIT_OK and conn.closed
    assert seen["key"] == FAKE_KEY and seen["url"] == FAKE_URL
    assert seen["options"]["autocommit"] is True
    day, listings = FakeJob.last.symbol_list
    assert day == date(2026, 9, 28) and "AAPL" in listings
    assert FakeJob.last.ticks == [NOW, NOW]
    assert any("tcp_keepalives_idle" in s for s in conn.statements)


def test_lost_connection_during_a_tick_exits_3(env):
    class Failing(FakeJob):
        def tick(self, now):
            raise psycopg.OperationalError("gone")

    code, _, conn = run(job_factory=Failing, max_ticks=3)
    assert code == runner.EXIT_DATABASE_LOST and conn.closed


def test_closed_connection_exits_3(env):
    conn = FakeConn()
    conn.closed = True
    code, _, _ = run(conn=conn, max_ticks=3)
    assert code == runner.EXIT_DATABASE_LOST


def test_provider_errors_inside_ticks_do_not_exit(env):
    # A real job over an in-memory store: every quote fails, the loop keeps going.
    fake = FakeMarketData()
    fake.add("AAPL")
    fake.fail("get_quote", error=ProviderUnavailable("down"))
    store = MemoryStore([Candidate("AAPL", "position", None, None)])

    def job_factory(provider, _store, config, **kwargs):
        return ReferenceJob(provider, store, config, **kwargs)

    code, _, _ = run(fake=fake, job_factory=job_factory, max_ticks=3)
    assert code == runner.EXIT_OK
    assert fake.calls_for("AAPL") == ["get_profile", "get_quote"]
    assert store.rows == {}


def test_reads_only_its_own_two_variables(env, monkeypatch):
    read = []

    class Recording(dict):
        def get(self, name, default=None):
            read.append(name)
            return super().get(name, default)

        def __getitem__(self, name):
            read.append(name)
            return super().__getitem__(name)

    monkeypatch.setattr(os, "environ", Recording(os.environ))
    run(max_ticks=1)
    assert set(read) == {runner.KEY_VARIABLE, runner.DATABASE_VARIABLE}


def test_unknown_arguments_refuse(env):
    assert runner.main(["--nope"]) == runner.EXIT_REFUSED
