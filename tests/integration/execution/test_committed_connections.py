"""Analyze S1 / ADR 0013 in production shape: Execution and the gate on two
separate autocommit connections, against a real, committed database.

The rest of the suite shares one rolled-back connection and switches roles, which
can't show whether Execution's writes are actually committed and visible to the
gate's own process. This test can.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.fakes.broker import FakeBroker
from tests.integration.execution.conftest import REPO_CONFIG
from trading_agent.execution.service import Executor, NotAutocommit
from trading_agent.risk.runner import evaluate_pending_triggers
from trading_agent.storage.migrate import apply_migrations

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)


@pytest.fixture
def committed_db(make_database):
    url = make_database()  # dropped at the end of the session
    apply_migrations(url)
    return url


def _connect(url, role=None):
    conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
    if role:
        conn.execute(f"SET ROLE {role}")
    return conn


def test_a_stop_loss_exit_across_two_processes_is_committed_and_visible(committed_db):
    with _connect(committed_db) as admin:
        admin.execute(
            "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
            "VALUES ('2026-09-28 12:00+00', 100000, 100000, 100000)"
        )
        admin.execute(
            "INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('AAPL', 50, 200)"
        )
    broker = FakeBroker(now=NOW)
    broker.set_position("AAPL", 50, "200")
    broker.set_trade("AAPL", "150")

    with (
        _connect(committed_db, "ta_execution") as exec_conn,
        _connect(committed_db, "ta_risk_gate") as gate_conn,
    ):
        executor = Executor(broker, exec_conn, REPO_CONFIG)  # no savepoint allowance
        executor.startup()
        assert executor.tick(NOW).triggers == 1
        assert evaluate_pending_triggers(gate_conn, NOW, REPO_CONFIG) == 1
        later = NOW + timedelta(minutes=1)
        broker.set_trade("AAPL", "150", at=later)
        assert executor.tick(later).submitted == 1
        # Still connected: a lock left held would show here (research E5).
        with _connect(committed_db) as observer:
            held = observer.execute(
                "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory'"
            ).fetchone()["n"]
        assert held == 0

    assert [(r.side, r.qty) for r in broker.submissions] == [("sell", 50)]
    with _connect(committed_db) as observer:
        counts = observer.execute(
            "SELECT (SELECT count(*) FROM stop_loss_triggers) AS triggers, "
            "(SELECT count(*) FROM risk_verdicts WHERE verdict = 'approved') AS verdicts, "
            "(SELECT count(*) FROM orders) AS orders"
        ).fetchone()
    assert counts == {"triggers": 1, "verdicts": 1, "orders": 1}


def test_startup_refuses_a_connection_that_is_not_autocommit(committed_db):
    with psycopg.connect(committed_db, row_factory=dict_row) as conn:
        with pytest.raises(NotAutocommit):
            Executor(FakeBroker(now=NOW), conn, REPO_CONFIG).startup()
