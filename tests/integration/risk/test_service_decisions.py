"""US1 through the service: decisions become recorded, idempotent verdicts."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from tests.integration.helpers import as_role
from tests.integration.risk.conftest import (
    NOW,
    TODAY,
    make_decision,
    seed_open_day,
    verdict_count,
)
from trading_agent.risk.config import RiskConfigError, load_config
from trading_agent.risk.service import evaluate_decision


def test_buy_recorded_once_with_trading_day_and_config_version(conn, repo_config):
    seed_open_day(conn)
    decision = make_decision(conn)
    with as_role(conn, "ta_risk_gate"):
        verdict = evaluate_decision(conn, decision, now=NOW, config_path=repo_config)

    assert verdict.approved and verdict.order.qty == 24
    assert verdict.order.limit_price == Decimal("202.00")
    row = conn.execute("SELECT * FROM risk_verdicts WHERE decision_id = %s", (decision,)).fetchone()
    assert row["trading_day"] == TODAY
    assert row["config_version"] == load_config(repo_config).version
    stored = row["approved_order"]
    stored = stored if isinstance(stored, dict) else json.loads(stored)
    assert stored["qty"] == 24 and stored["limit_price"] == "202.00"
    assert stored["exposure"] == "increase" and stored["order_type"] == "limit"


def test_second_evaluation_returns_the_same_verdict_and_writes_nothing(conn, repo_config):
    seed_open_day(conn)
    decision = make_decision(conn)
    with as_role(conn, "ta_risk_gate"):
        first = evaluate_decision(conn, decision, now=NOW, config_path=repo_config)
        count = verdict_count(conn)
        second = evaluate_decision(conn, decision, now=NOW, config_path=repo_config)
        assert verdict_count(conn) == count
    assert first == second


def test_hold_decision_produces_nothing(conn, repo_config):
    seed_open_day(conn)
    decision = make_decision(conn, direction="hold")
    with as_role(conn, "ta_risk_gate"):
        assert evaluate_decision(conn, decision, now=NOW, config_path=repo_config) is None
    assert verdict_count(conn) == 0


def test_missing_config_raises_and_writes_nothing(conn, tmp_path):
    seed_open_day(conn)
    decision = make_decision(conn)
    with pytest.raises(RiskConfigError), as_role(conn, "ta_risk_gate"):
        evaluate_decision(conn, decision, now=NOW, config_path=tmp_path / "missing.yaml")
    assert verdict_count(conn) == 0
