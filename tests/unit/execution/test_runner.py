"""US4: the runner refuses to start unless on the paper account, and exits on a
lost database connection (ADR 0013). Everything is stubbed; no network, no DB."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from tests.fakes.broker import FakeBroker
from trading_agent.execution import __main__ as runner
from trading_agent.execution.broker import NotPaperTrading
from trading_agent.execution.service import Executor, NotAutocommit

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)


class RecordingConn:
    """Fails the test if anything is asked of it; records that it was asked."""

    def __init__(self, autocommit=True, log=None):
        self.autocommit = autocommit
        self.closed = False
        self.log = log if log is not None else []

    def cursor(self, *a, **k):
        self.log.append("db")
        raise AssertionError("database touched")

    def transaction(self, *a, **k):
        self.log.append("db")
        raise AssertionError("database touched")

    def execute(self, statement, *args):
        self.log.append(statement)

    def close(self):
        self.closed = True


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    monkeypatch.setenv("EXECUTION_DATABASE_URL", "postgresql://localhost/x")
    monkeypatch.delenv("ALPACA_BASE_URL", raising=False)
    monkeypatch.delenv("RISK_GATE_DATABASE_URL", raising=False)


def test_startup_verifies_paper_before_touching_the_broker_or_database():
    broker = FakeBroker()
    conn = RecordingConn()
    broker.fail("verify_paper")
    with pytest.raises(NotPaperTrading):
        Executor(broker, conn).startup()
    assert broker.calls == ["verify_paper"] and conn.log == []


def test_startup_and_tick_refuse_a_connection_that_is_not_autocommit():
    broker = FakeBroker()
    with pytest.raises(NotAutocommit):
        Executor(broker, RecordingConn(autocommit=False)).startup()
    with pytest.raises(NotAutocommit):
        Executor(broker, RecordingConn(autocommit=False)).tick(NOW)


def test_the_runner_exits_refused_when_not_on_paper(env):
    broker = FakeBroker()
    broker.fail("verify_paper")
    conn = RecordingConn()
    code = runner.main(broker_factory=lambda *a: broker, connect=lambda *a, **k: conn, max_ticks=1)
    assert code == runner.EXIT_REFUSED and conn.log == [] and conn.closed


def test_a_refused_base_url_exits_before_connecting(env, monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    connected = []
    code = runner.main(connect=lambda *a, **k: connected.append(1), max_ticks=1)
    assert code == runner.EXIT_REFUSED and connected == []


def test_a_missing_key_is_refused_by_name_never_by_value(env, monkeypatch, caplog):
    monkeypatch.delenv("ALPACA_API_SECRET_KEY")
    code = runner.main(broker_factory=lambda *a: FakeBroker(), max_ticks=1)
    assert code == runner.EXIT_REFUSED
    assert "ALPACA_API_SECRET_KEY" in caplog.text


@pytest.mark.parametrize(
    "missing", ["ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "EXECUTION_DATABASE_URL"]
)
def test_each_required_variable_is_refused_by_name_and_no_value_is_logged(
    env, monkeypatch, caplog, missing
):
    # Distinctive values, so a leak of any variable that is set would show.
    monkeypatch.setenv("ALPACA_API_KEY_ID", "leakcheck-key-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "leakcheck-secret")
    monkeypatch.setenv("EXECUTION_DATABASE_URL", "postgresql://leakcheck-login@localhost/x")
    monkeypatch.delenv(missing)
    connected = []
    code = runner.main(
        broker_factory=lambda *a: FakeBroker(),
        connect=lambda *a, **k: connected.append(1),
        max_ticks=1,
    )
    assert code == runner.EXIT_REFUSED and connected == []
    assert missing in caplog.text and "leakcheck" not in caplog.text


def test_the_runner_connects_with_autocommit(env):
    seen = {}

    class Stub:
        def __init__(self, broker, conn):
            pass

        def startup(self):
            pass

        def tick(self, now):
            return "report"

    def connect(url, **kwargs):
        seen.update(kwargs)
        return RecordingConn()

    code = runner.main(
        broker_factory=lambda *a: FakeBroker(),
        connect=connect,
        executor_factory=Stub,
        clock=lambda: NOW,
        max_ticks=2,
        sleep=lambda s: None,
    )
    assert code == runner.EXIT_OK and seen["autocommit"] is True
    # Second review F8: a dead peer is noticed in about a minute, both ways.
    assert (seen["keepalives"], seen["keepalives_idle"]) == (1, 30)


def test_a_lost_database_connection_exits_for_a_restart(env):
    class Dies:
        def __init__(self, broker, conn):
            pass

        def startup(self):
            pass

        def tick(self, now):
            raise psycopg.OperationalError("server closed the connection")

    code = runner.main(
        broker_factory=lambda *a: FakeBroker(),
        connect=lambda *a, **k: RecordingConn(),
        executor_factory=Dies,
        max_ticks=5,
        sleep=lambda s: None,
    )
    assert code == runner.EXIT_DATABASE_LOST


def test_the_runner_never_reads_the_risk_gates_credential():
    source = Path(runner.__file__).read_text()
    strings = {n.value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Constant)}
    assert "RISK_GATE_DATABASE_URL" not in strings


def test_startup_waits_for_the_single_instance_lock_before_giving_up():
    # Second review F8: a previous process's lock can outlive it briefly.
    from datetime import timedelta

    from trading_agent.execution.service import AnotherExecutionRunning

    class LockConn(RecordingConn):
        def __init__(self, free_after):
            super().__init__()
            self.tries, self.free_after = 0, free_after

        def cursor(self, *a, **k):
            conn = self

            class Cur:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def execute(self, *a):
                    conn.tries += 1

                def fetchone(self):
                    return {"mine": conn.tries > conn.free_after}

            return Cur()

    slept = []
    conn = LockConn(free_after=2)
    executor = Executor(FakeBroker(), conn)
    executor._positions_pass = lambda report: None
    executor.startup(
        lock_wait=timedelta(seconds=60), lock_retry=timedelta(seconds=15), sleep=slept.append
    )
    assert conn.tries == 3 and slept == [15.0, 15.0]

    never = LockConn(free_after=99)
    with pytest.raises(AnotherExecutionRunning):
        Executor(FakeBroker(), never).startup(
            lock_wait=timedelta(seconds=30), lock_retry=timedelta(seconds=15), sleep=slept.append
        )
    assert never.tries == 3
