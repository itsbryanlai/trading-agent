"""specs/001-data-model/contracts/role-grants.md, as data.

This is the contract. test_grants.py asserts the database matches it exactly,
in both directions: every listed op is allowed, every unlisted op is denied,
and the catalogs hold no privilege for a ta_* role that isn't listed here.

Ops: "S" SELECT, "I" INSERT, "U" UPDATE, "D" DELETE, "U:<col>" column-level UPDATE,
"S:<col>" column-level SELECT.
"""

ROLES = (
    "ta_research",
    "ta_opportunistic_identifier",
    "ta_portfolio_manager",
    "ta_risk_gate",
    "ta_execution",
    "ta_journal",
    "ta_orchestrator",
    "ta_assistant",
    "ta_dashboard",
    "ta_dashboard_control",
    "ta_reference_data",
)

OPS = ("S", "I", "U", "D")

# object -> role -> ops. Roles absent from an object's dict have no access to it.
GRANTS: dict[str, dict[str, set[str]]] = {
    # US1
    "reports": {
        "ta_research": {"S", "I"},
        "ta_opportunistic_identifier": {"S", "I"},
        "ta_portfolio_manager": {"S"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # US2
    "decisions": {
        "ta_portfolio_manager": {"S", "I"},
        "ta_risk_gate": {"S"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    "decision_reports": {
        "ta_portfolio_manager": {"S", "I"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    "risk_verdicts": {
        "ta_risk_gate": {"S", "I"},
        "ta_execution": {"S"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # UPDATE narrowed by 003 to the columns that change after submission.
    "orders": {
        "ta_execution": {
            "S",
            "I",
            "U:broker_order_id",
            "U:status",
            "U:fill_qty",
            "U:fill_price",
            "U:broker_reason",
            "U:updated_at",
        },
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    "reports_with_status": {
        "ta_portfolio_manager": {"S"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # US3
    "positions": {
        "ta_execution": {"S", "I", "U", "D"},
        "ta_portfolio_manager": {"S"},
        "ta_risk_gate": {"S"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    "account_snapshots": {
        "ta_execution": {"S", "I"},
        "ta_portfolio_manager": {"S"},
        "ta_risk_gate": {"S"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # ta_risk_gate and ta_execution deliberately absent: attribution must never
    # become a trading input (FR-012).
    "journal": {
        "ta_journal": {"S", "I", "U"},
        "ta_portfolio_manager": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # US4. Column-level: each writer may touch only its own columns.
    "system_state": {
        "ta_risk_gate": {
            "S",
            "U:halt_triggered_on",
            "U:baseline_trading_day",
            "U:daily_starting_equity",
            "U:updated_at",
        },
        "ta_dashboard_control": {"S", "U:trading_paused", "U:updated_at"},
        # 003: the manual pause flag only (FR-018); never the halt or baseline.
        "ta_execution": {"S:trading_paused"},
        # 005: the pause flag only; never the equity or the halt (research O7).
        "ta_orchestrator": {"S:trading_paused"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    "system_state_effective": {
        "ta_risk_gate": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
        "ta_dashboard_control": {"S"},
    },
    # Feature 002 (migration 0006). Execution records stop-loss observations; the
    # Risk Gate evaluates them. The reference-data job writes universe data.
    "stop_loss_triggers": {
        "ta_execution": {"S", "I"},
        "ta_risk_gate": {"S"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # Feature 004 (migration 0009) revoked the job's UPDATE: rows are never changed.
    "instrument_reference": {
        "ta_reference_data": {"S", "I"},
        "ta_risk_gate": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # Feature 003 (migration 0007). An approval Execution declined to submit.
    "execution_refusals": {
        "ta_execution": {"S", "I"},
        "ta_journal": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # Feature 004 (migration 0009). The reference-data job's only read outside its
    # own table: symbols and times, never the base tables.
    "reference_candidate_symbols": {
        "ta_reference_data": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    # Feature 005 (migration 0010). The orchestrator's own run records, and the only
    # thing it may learn about reports: the latest creation time.
    "orchestrator_runs": {
        "ta_orchestrator": {"S", "I", "U:pgid", "U:finished_at", "U:outcome", "U:detail"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    "latest_report_time": {
        "ta_orchestrator": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
}
