"""ADR 0013: the gate's trigger runner loop exits on a lost database connection
and holds only the gate's credential. Stubbed; no database."""

from __future__ import annotations

import ast
from pathlib import Path

import psycopg
import pytest

from trading_agent.risk import __main__ as runner


class Conn:
    closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RISK_GATE_DATABASE_URL", "postgresql://localhost/x")


def test_passes_run_on_an_autocommit_connection(env):
    seen, calls = {}, []

    def connect(url, **kwargs):
        seen.update(kwargs)
        return Conn()

    code = runner.main(
        connect=connect,
        evaluate=lambda conn, now: calls.append(now),
        sleep=lambda s: None,
        max_passes=3,
    )
    assert code == runner.EXIT_OK and len(calls) == 3 and seen["autocommit"] is True


def test_a_lost_connection_exits_for_a_restart(env):
    def dies(conn, now):
        raise psycopg.OperationalError("server closed the connection")

    code = runner.main(connect=lambda *a, **k: Conn(), evaluate=dies, max_passes=5)
    assert code == runner.EXIT_DATABASE_LOST


def test_a_missing_credential_refuses_to_start(monkeypatch):
    monkeypatch.delenv("RISK_GATE_DATABASE_URL", raising=False)
    assert runner.main(connect=lambda *a, **k: Conn(), max_passes=1) == runner.EXIT_REFUSED


def test_the_gate_runner_never_touches_broker_credentials():
    source = (
        Path(runner.__file__).read_text() + Path(runner.__file__).with_name("runner.py").read_text()
    )
    strings = {n.value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Constant)}
    assert not any(isinstance(s, str) and s.startswith("ALPACA") for s in strings)
    assert "EXECUTION_DATABASE_URL" not in strings
