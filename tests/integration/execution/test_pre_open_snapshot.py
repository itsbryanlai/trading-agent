"""US6: one account snapshot before every trading day's open, and Execution's
baseline equals the gate's (FR-015, research E11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.integration.execution.conftest import REPO_CONFIG, run_tick
from tests.integration.helpers import as_role
from tests.integration.risk.conftest import insert_reference, make_decision
from trading_agent.execution.service import baseline_equity
from trading_agent.risk.service import evaluate_decision

PRE_OPEN = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)  # 09:00 ET


def _snapshots(conn):
    return conn.execute("SELECT taken_at, equity FROM account_snapshots").fetchall()


def test_one_snapshot_is_recorded_before_the_open(conn, broker, executor):
    broker.set_account(equity="101000", cash="50000", buying_power="100000")
    report = run_tick(conn, executor, PRE_OPEN)
    run_tick(conn, executor, PRE_OPEN + timedelta(minutes=10))
    assert report.snapshot_taken
    assert [(s["taken_at"], s["equity"]) for s in _snapshots(conn)] == [
        (PRE_OPEN, Decimal("101000.00"))
    ]


def test_a_broker_failure_is_retried_on_the_next_tick(conn, broker, executor):
    broker.fail("get_account")
    assert not run_tick(conn, executor, PRE_OPEN).snapshot_taken
    assert _snapshots(conn) == []
    assert run_tick(conn, executor, PRE_OPEN + timedelta(minutes=1)).snapshot_taken


def test_no_snapshot_on_a_weekend_or_holiday(conn, broker, executor):
    run_tick(conn, executor, datetime(2026, 9, 26, 13, 0, tzinfo=UTC))
    run_tick(conn, executor, datetime(2026, 11, 26, 14, 0, tzinfo=UTC))
    assert _snapshots(conn) == []


def test_none_outside_the_hour_before_the_open(conn, broker, executor):
    run_tick(conn, executor, datetime(2026, 9, 28, 12, 29, tzinfo=UTC))
    run_tick(conn, executor, datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    assert _snapshots(conn) == []


def test_the_gate_takes_its_baseline_from_it_and_both_agree(conn, broker, executor):
    broker.set_account(equity="101000", cash="101000")
    run_tick(conn, executor, PRE_OPEN)
    insert_reference(conn)
    decision = make_decision(conn)
    now = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    with as_role(conn, "ta_risk_gate"):
        evaluate_decision(conn, decision, now=now, config_path=REPO_CONFIG)

    gate = conn.execute("SELECT daily_starting_equity FROM system_state").fetchone()
    with conn.cursor() as cur:
        ours = baseline_equity(cur, now.date())
    assert gate["daily_starting_equity"] == ours == Decimal("101000.00")
