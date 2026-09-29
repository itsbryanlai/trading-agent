"""Structural guards for the reference-data job (specs/004-reference-data D1, D14, FR-020).

1. Nothing in trading_agent.reference imports the broker SDK, Execution, or the
   gate's decision code, and no module names another component's credential.
2. The pure core does no I/O: no database driver, no HTTP, no environment.
"""

from __future__ import annotations

import ast
from pathlib import Path

REFERENCE = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "reference"
CORE_MODULES = ("normalize.py", "symbols.py", "schedule.py")
FORBIDDEN_ANYWHERE = (
    "alpaca",
    "trading_agent.execution",
    "trading_agent.risk.service",
    "trading_agent.risk.gate",
    "trading_agent.risk.rules",
)
FORBIDDEN_IN_CORE = ("psycopg", "urllib", "os", "trading_agent.reference.service")
OTHER_COMPONENTS_VARIABLES = (
    "ALPACA_",
    "EXECUTION_DATABASE_URL",
    "RISK_GATE_DATABASE_URL",
    "ADMIN_DATABASE_URL",
)


def _modules() -> list[Path]:
    return sorted(REFERENCE.glob("*.py"))


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
    assert (REFERENCE / "__init__.py").exists()


def test_no_module_imports_the_broker_or_the_gates_decision_code():
    for path in _modules():
        bad = {n for n in _imports(path) for f in FORBIDDEN_ANYWHERE if _matches(n, f)}
        assert not bad, f"{path.name} imports {bad}"


def test_no_module_names_another_components_credential():
    for path in _modules():
        text = path.read_text()
        for name in OTHER_COMPONENTS_VARIABLES:
            assert name not in text, f"{path.name} mentions {name}"


def test_the_pure_core_does_no_io():
    for name in CORE_MODULES:
        path = REFERENCE / name
        if not path.exists():
            continue  # built in a later task
        bad = {n for n in _imports(path) for f in FORBIDDEN_IN_CORE if _matches(n, f)}
        assert not bad, f"{name} imports {bad}"
