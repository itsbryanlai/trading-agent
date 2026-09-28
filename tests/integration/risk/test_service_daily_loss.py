"""US3 through the service: the baseline and the halt are persisted with the verdict."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.integration.helpers import as_role
from tests.integration.risk.conftest import (
    INTRADAY,
    NOW,
    PRE_OPEN,
    TODAY,
    insert_reference,
    insert_snapshot,
    make_decision,
)
from trading_agent.risk.service import evaluate_decision


def _state(conn):
    return conn.execute("SELECT * FROM system_state").fetchone()


def _evaluate(conn, decision, repo_config):
    with as_role(conn, "ta_risk_gate"):
        return evaluate_decision(conn, decision, now=NOW, config_path=repo_config)


def test_first_evaluation_records_the_pre_open_baseline_once(conn, repo_config):
    insert_snapshot(conn, PRE_OPEN, equity="100000")
    insert_snapshot(conn, INTRADAY, equity="99000", cash="99000")
    insert_reference(conn)

    _evaluate(conn, make_decision(conn), repo_config)
    state = _state(conn)
    assert state["baseline_trading_day"] == TODAY
    assert state["daily_starting_equity"] == Decimal("100000")

    insert_snapshot(conn, PRE_OPEN.replace(minute=30), equity="123456")
    _evaluate(conn, make_decision(conn, symbol="AAPL", target="6"), repo_config)
    assert _state(conn)["daily_starting_equity"] == Decimal("100000")


def test_crossing_the_line_records_the_halt_and_touches_no_position(conn, repo_config):
    insert_snapshot(conn, PRE_OPEN, equity="100000")
    insert_snapshot(conn, INTRADAY, equity="80000", cash="80000")
    insert_reference(conn)
    conn.execute("INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('MSFT', 10, 400)")
    before = conn.execute("SELECT * FROM positions").fetchall()

    verdict = _evaluate(conn, make_decision(conn), repo_config)

    assert verdict.rejection_rule == "daily_loss_halt"
    assert _state(conn)["halt_triggered_on"] == TODAY
    assert conn.execute("SELECT * FROM positions").fetchall() == before


def test_no_pre_open_snapshot_means_no_baseline_and_nothing_recorded(conn, repo_config):
    insert_snapshot(conn, INTRADAY)
    insert_reference(conn)
    verdict = _evaluate(conn, make_decision(conn), repo_config)
    assert verdict.rejection_rule == "no_daily_baseline"
    state = _state(conn)
    assert state["baseline_trading_day"] is None and state["halt_triggered_on"] is None


def test_a_previous_days_halt_does_not_block_today(conn, repo_config):
    insert_snapshot(conn, PRE_OPEN)
    insert_snapshot(conn, INTRADAY)
    insert_reference(conn)
    conn.execute("UPDATE system_state SET halt_triggered_on = %s", (date(2026, 9, 25),))
    assert _evaluate(conn, make_decision(conn), repo_config).approved


def test_a_crossing_between_evaluations_still_records_the_halt(conn, repo_config):
    # ADR 0014 §3 (second review F7): a window snapshot at 09:45 ET showed the
    # crossing; by 09:55 equity recovered; the next evaluation still halts.
    insert_snapshot(conn, PRE_OPEN, equity="100000")
    insert_snapshot(conn, INTRADAY, equity="79000", cash="79000")
    insert_snapshot(conn, INTRADAY.replace(minute=55), equity="95000", cash="95000")
    insert_reference(conn)

    verdict = _evaluate(conn, make_decision(conn), repo_config)

    assert verdict.rejection_rule == "daily_loss_halt"
    assert _state(conn)["halt_triggered_on"] == TODAY
