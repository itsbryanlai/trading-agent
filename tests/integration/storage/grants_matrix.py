"""specs/001-data-model/contracts/role-grants.md, as data.

This is the contract. test_grants.py asserts the database matches it exactly,
in both directions: every listed op is allowed, every unlisted op is denied,
and the catalogs hold no privilege for a ta_* role that isn't listed here.

Ops: "S" SELECT, "I" INSERT, "U" UPDATE, "D" DELETE, "U:<col>" column-level UPDATE.
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
    "orders": {
        "ta_execution": {"S", "I", "U"},
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
        "ta_orchestrator": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
    },
    "system_state_effective": {
        "ta_risk_gate": {"S"},
        "ta_orchestrator": {"S"},
        "ta_assistant": {"S"},
        "ta_dashboard": {"S"},
        "ta_dashboard_control": {"S"},
    },
}
