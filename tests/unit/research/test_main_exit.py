"""Exit codes after startup (specs/007-research-agent research R9; FR-017; analyze G2)."""

from __future__ import annotations

import logging

from tests.fakes.model import FakeModel
from tests.unit.research.test_main import SECRETS, FakeConn, env, run  # noqa: F401 (fixture)
from trading_agent.research import __main__ as runner
from trading_agent.research import service
from trading_agent.research.ports import ModelUnavailable


def test_a_recorded_failure_is_exit_1(env):
    code, conn, *_ = run(model=FakeModel(error=ModelUnavailable()))
    assert code == runner.EXIT_FAILURE_RECORDED
    (written,) = conn.written
    assert written[0] is None and written[1] == "no_action"


def test_a_failed_write_is_exit_3(env, caplog):
    code, conn, *_ = run(conn=FakeConn(fail_write=True))
    assert code == runner.EXIT_DATABASE
    assert "database error: OperationalError" in caplog.text
    assert conn.closed


def test_a_failed_read_of_open_reports_is_exit_3(env):
    code, conn, *_ = run(conn=FakeConn(fail_read=True))
    assert code == runner.EXIT_DATABASE
    assert conn.written == []


def test_a_crash_is_exit_4_not_pythons_1(env, monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def crash(self):
        raise RuntimeError("fake-not-real secret-looking text")

    monkeypatch.setattr(service.ResearchRun, "run", crash)
    code, conn, printed, _ = run()
    assert code == runner.EXIT_CRASHED == 4
    assert "research: crashed: RuntimeError" in caplog.text
    text = caplog.text + "\n".join(printed)
    for secret in SECRETS:
        assert secret not in text
    assert conn.closed


def test_the_write_is_the_only_database_mutation(env):
    """Stopping at any point is safe: the run's only write is one batch (R2, R8)."""
    statements: list[str] = []

    class Recording(FakeConn):
        def execute(self, statement, params=None):
            statements.append(statement)
            return super().execute(statement, params)

    code, conn, *_ = run(conn=Recording())
    assert code == runner.EXIT_OK
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert len(conn.written) == 1
