"""Structural guards for the orchestrator (specs/005-orchestrator research O1, O4).

1. It imports no model SDK, no broker SDK and no trading component, so it can't
   make a model call or reach a trade (FR-002, FR-003, ADR 0003).
2. No module names another component's credential.
3. The planner does no I/O: no database, processes, environment or signals.
"""

from __future__ import annotations

import ast
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "orchestrator"
PLANNER = ORCHESTRATOR / "planner.py"
FORBIDDEN_ANYWHERE = (
    "alpaca",
    "anthropic",
    "openai",
    "trading_agent.execution",
    "trading_agent.reference",
    "trading_agent.risk.service",
    "trading_agent.risk.gate",
    "trading_agent.risk.rules",
)
FORBIDDEN_IN_PLANNER = (
    "psycopg",
    "subprocess",
    "os",
    "signal",
    "trading_agent.orchestrator.service",
    "trading_agent.orchestrator.launcher",
)
OTHER_COMPONENTS_VARIABLES = (
    "ALPACA_",
    "EXECUTION_DATABASE_URL",
    "RISK_GATE_DATABASE_URL",
    "ADMIN_DATABASE_URL",
    "REFERENCE_DATA_",
)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _matches(name: str, forbidden: str) -> bool:
    return name == forbidden or name.startswith(forbidden + ".")


def test_the_package_exists():
    assert (ORCHESTRATOR / "__init__.py").exists()


def test_no_model_sdk_broker_or_trading_component():
    for path in sorted(ORCHESTRATOR.glob("*.py")):
        bad = {n for n in _imports(path) for f in FORBIDDEN_ANYWHERE if _matches(n, f)}
        assert not bad, f"{path.name} imports {bad}"


def test_no_module_names_another_components_credential():
    for path in sorted(ORCHESTRATOR.glob("*.py")):
        text = path.read_text()
        for name in OTHER_COMPONENTS_VARIABLES:
            assert name not in text, f"{path.name} mentions {name}"


def test_the_planner_does_no_io():
    if not PLANNER.exists():
        return  # built in a later task
    bad = {n for n in _imports(PLANNER) for f in FORBIDDEN_IN_PLANNER if _matches(n, f)}
    assert not bad, f"planner.py imports {bad}"
