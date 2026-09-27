"""Fixtures for Execution integration tests, on the test clock:
Monday 2026-09-28, open 13:30 UTC, close 20:00 UTC, "now" 14:00 UTC (10:00 ET).

Rows are seeded as the admin inside the rolled-back `conn`; Execution runs as
ta_execution through `as_role`, against the in-memory fake broker. No test here
reaches a real broker (tests/conftest.py blocks the network).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.fakes.broker import FakeBroker
from tests.integration.helpers import as_role
from tests.integration.risk.conftest import (
    PRE_OPEN,
    REPO_CONFIG,
    insert_position,
    insert_snapshot,
    make_decision,
)
from trading_agent.execution.service import Executor
from trading_agent.risk.model import ApprovedOrder

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
TODAY = date(2026, 9, 28)

__all__ = ["PRE_OPEN", "REPO_CONFIG", "insert_position", "insert_snapshot"]


def buy_order(qty=24, ceiling="202", symbol="AAPL", day: date = TODAY) -> ApprovedOrder:
    return ApprovedOrder(
        symbol=symbol,
        side="buy",
        qty=qty,
        order_type="limit",
        limit_price=Decimal(ceiling),
        trading_day=day,
        exposure="increase",
        source="decision",
    )


def sell_order(qty=50, symbol="AAPL", day: date = TODAY, source="decision") -> ApprovedOrder:
    return ApprovedOrder(
        symbol=symbol,
        side="sell",
        qty=qty,
        order_type="market",
        limit_price=None,
        trading_day=day,
        exposure="decrease",
        source=source,
    )


def approved_verdict(
    conn: psycopg.Connection,
    order: ApprovedOrder,
    *,
    approved: bool = True,
    verdict_id: UUID | None = None,
) -> UUID:
    """An approved (or rejected) verdict carrying `order`, as the gate would write it."""
    if order.source == "stop_loss":
        trigger = conn.execute(
            "INSERT INTO stop_loss_triggers (symbol, observed_price) VALUES (%s, 150) RETURNING id",
            (order.symbol,),
        ).fetchone()["id"]
        source = (None, trigger)
    else:
        direction = order.side
        source = (make_decision(conn, direction=direction, symbol=order.symbol), None)
    row = conn.execute(
        """
        INSERT INTO risk_verdicts (id, decision_id, stop_loss_trigger_id, verdict,
                                   rejection_rule, approved_order, trading_day, config_version)
        VALUES (coalesce(%s, gen_random_uuid()), %s, %s, %s, %s, %s, %s, 'test')
        RETURNING id
        """,
        (
            verdict_id,
            *source,
            "approved" if approved else "rejected",
            None if approved else "max_position_pct",
            Jsonb(order.to_json()) if approved else None,
            order.trading_day,
        ),
    ).fetchone()
    return row["id"]


def seed_baseline(conn, equity="100000") -> None:
    insert_snapshot(conn, PRE_OPEN, equity, equity)


def set_paused(conn, paused: bool = True) -> None:
    conn.execute("UPDATE system_state SET trading_paused = %s", (paused,))


def outcomes(conn, verdict_id: UUID) -> tuple[list[dict], list[dict]]:
    orders = conn.execute(
        "SELECT * FROM orders WHERE risk_verdict_id = %s", (verdict_id,)
    ).fetchall()
    refusals = conn.execute(
        "SELECT * FROM execution_refusals WHERE risk_verdict_id = %s", (verdict_id,)
    ).fetchall()
    return orders, refusals


@pytest.fixture
def broker() -> FakeBroker:
    fake = FakeBroker(now=NOW)
    fake.set_quote("AAPL", "201.50")
    return fake


@pytest.fixture
def executor(conn, broker) -> Executor:
    # The rolled-back harness runs inside savepoints; production requires autocommit.
    return Executor(broker, conn, REPO_CONFIG, _allow_savepoints=True)


def run_tick(conn, executor: Executor, now: datetime = NOW):
    with as_role(conn, "ta_execution"):
        return executor.tick(now)


def gate_runner_pass(conn, now: datetime = NOW, config=REPO_CONFIG) -> int:
    """One pass of the gate's own trigger runner, as ta_risk_gate (ADR 0013)."""
    from trading_agent.risk.runner import evaluate_pending_triggers

    with as_role(conn, "ta_risk_gate"):
        return evaluate_pending_triggers(conn, now, config)
