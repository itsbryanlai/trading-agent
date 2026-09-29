"""Probe statements for the grants test, one per (object, op).

Postgres checks privileges when a statement starts executing, before it looks
at a single row. So every probe touches zero rows (`WHERE false`, or an INSERT
from an empty SELECT): it proves whether the role *may* do the thing without
needing valid data, satisfying constraints, or seeding prerequisites.

Row-level security policies only apply to rows actually written, so these
probes test grants alone; RLS is tested with real rows in test_reports.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from psycopg import sql


@dataclass(frozen=True)
class ObjectSpec:
    kind: Literal["table", "view"]
    # A nullable-or-not column used as the target of zero-row INSERT/UPDATE probes.
    probe_column: str
    # For tables granted UPDATE per column: every column, so each is probed.
    columns_for_update: tuple[str, ...] = ()
    # For tables granted SELECT per column: every column, so each is probed.
    columns_for_select: tuple[str, ...] = ()


# object name -> spec. Stories add their objects here alongside grants_matrix.GRANTS.
OBJECTS: dict[str, ObjectSpec] = {
    # US1
    "reports": ObjectSpec("table", probe_column="rationale_md"),
    # US2
    "decisions": ObjectSpec("table", probe_column="reasoning_md"),
    "decision_reports": ObjectSpec("table", probe_column="report_id"),
    "risk_verdicts": ObjectSpec("table", probe_column="rejection_rule"),
    "orders": ObjectSpec(
        "table",
        probe_column="broker_order_id",
        columns_for_update=(
            "id",
            "risk_verdict_id",
            "verdict",
            "submitted_at",
            "broker_order_id",
            "status",
            "fill_price",
            "fill_qty",
            "updated_at",
            "limit_price",
            "broker_reason",
        ),
    ),
    "reports_with_status": ObjectSpec("view", probe_column="status"),
    # US3
    "positions": ObjectSpec("table", probe_column="qty"),
    "account_snapshots": ObjectSpec("table", probe_column="cash"),
    "journal": ObjectSpec("table", probe_column="summary_md"),
    # US4
    "system_state": ObjectSpec(
        "table",
        probe_column="trading_paused",
        columns_for_update=(
            "id",
            "trading_paused",
            "halt_triggered_on",
            "baseline_trading_day",
            "daily_starting_equity",
            "updated_at",
        ),
        columns_for_select=(
            "id",
            "trading_paused",
            "halt_triggered_on",
            "baseline_trading_day",
            "daily_starting_equity",
            "updated_at",
        ),
    ),
    "system_state_effective": ObjectSpec("view", probe_column="trading_paused"),
    # Feature 002
    "stop_loss_triggers": ObjectSpec("table", probe_column="observed_price"),
    "instrument_reference": ObjectSpec("table", probe_column="exchange_mic"),
    # Feature 003
    "execution_refusals": ObjectSpec("table", probe_column="reason"),
    # Feature 004
    "reference_candidate_symbols": ObjectSpec("view", probe_column="symbol"),
}


def ops_for(name: str) -> list[str]:
    spec = OBJECTS[name]
    if spec.kind == "view":
        return ["S"]
    updates = [f"U:{c}" for c in spec.columns_for_update] or ["U"]
    selects = [f"S:{c}" for c in spec.columns_for_select]
    return ["S", *selects, "I", *updates, "D"]


def probe(name: str, op: str) -> sql.Composable:
    spec = OBJECTS[name]
    table = sql.Identifier(name)
    if op == "S":
        # SELECT * needs every column, so a column-only grant reads as "denied"
        # here and "allowed" only for its own S:<col> probe.
        return sql.SQL("SELECT * FROM {} WHERE false").format(table)
    if op.startswith("S:"):
        return sql.SQL("SELECT {} FROM {} WHERE false").format(
            sql.Identifier(op.partition(":")[2]), table
        )
    if op == "I":
        return sql.SQL("INSERT INTO {} ({}) SELECT NULL WHERE false").format(
            table, sql.Identifier(spec.probe_column)
        )
    if op == "D":
        return sql.SQL("DELETE FROM {} WHERE false").format(table)
    if op.startswith("U"):
        column = op.partition(":")[2] or spec.probe_column
        return sql.SQL("UPDATE {} SET {} = NULL WHERE false").format(table, sql.Identifier(column))
    raise ValueError(f"unknown op {op}")
