"""US4 through the service: triggers recorded by Execution become verdicts."""

from __future__ import annotations

from tests.integration.helpers import as_role
from tests.integration.risk.conftest import (
    NOW,
    TODAY,
    insert_position,
    make_decision,
    seed_open_day,
    verdict_count,
)
from tests.integration.storage.chain import insert_order
from trading_agent.risk.service import evaluate_decision, evaluate_stop_loss_trigger


def _record_trigger(conn, observed: str, symbol="AAPL") -> str:
    with as_role(conn, "ta_execution"):
        return conn.execute(
            "INSERT INTO stop_loss_triggers (symbol, observed_price) VALUES (%s, %s) RETURNING id",
            (symbol, observed),
        ).fetchone()["id"]


def _evaluate(conn, trigger_id, repo_config):
    with as_role(conn, "ta_risk_gate"):
        return evaluate_stop_loss_trigger(conn, trigger_id, now=NOW, config_path=repo_config)


def test_breach_is_recorded_once_as_a_full_market_exit(conn, repo_config):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    trigger = _record_trigger(conn, "160")

    verdict = _evaluate(conn, trigger, repo_config)
    assert verdict.approved
    assert (verdict.order.qty, verdict.order.order_type) == (50, "market")

    row = conn.execute(
        "SELECT decision_id, stop_loss_trigger_id, trading_day FROM risk_verdicts "
        "WHERE stop_loss_trigger_id = %s",
        (trigger,),
    ).fetchone()
    assert row["decision_id"] is None and row["trading_day"] == TODAY

    count = verdict_count(conn)
    assert _evaluate(conn, trigger, repo_config) == verdict
    assert verdict_count(conn) == count


def test_non_breach_is_rejected(conn, repo_config):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    verdict = _evaluate(conn, _record_trigger(conn, "161"), repo_config)
    assert verdict.rejection_rule == "stop_loss_not_breached"


def test_exit_approved_through_a_full_cap_and_an_active_halt(conn, repo_config):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    decisions = []
    for n in range(5):  # fill the daily order cap with real, gate-approved buys
        conn.execute(
            "INSERT INTO instrument_reference (symbol, trading_day, security_type, exchange_mic, "
            "market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd) "
            "VALUES (%s, %s, 'common_stock', 'XNAS', 1e12, 1e10, 50)",
            (f"CAP{n}", TODAY),
        )
        decisions.append(make_decision(conn, symbol=f"CAP{n}", target="1", quote="50"))
    with as_role(conn, "ta_risk_gate"):
        for decision in decisions:
            assert evaluate_decision(conn, decision, now=NOW, config_path=repo_config).approved
    conn.execute("UPDATE system_state SET halt_triggered_on = %s", (TODAY,))
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM risk_verdicts WHERE approved_order->>'exposure' = 'increase'"
        ).fetchone()["n"]
        == 5
    )

    verdict = _evaluate(conn, _record_trigger(conn, "150"), repo_config)
    assert verdict.approved and verdict.order.qty == 50


def test_approved_exit_can_be_turned_into_an_order_by_execution(conn, repo_config):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    trigger = _record_trigger(conn, "150")
    _evaluate(conn, trigger, repo_config)
    verdict_id = conn.execute(
        "SELECT id FROM risk_verdicts WHERE stop_loss_trigger_id = %s", (trigger,)
    ).fetchone()["id"]
    with as_role(conn, "ta_execution"):
        assert insert_order(conn, verdict_id, side="sell")
