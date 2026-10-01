"""Structural guards for Research (specs/007-research-agent plan, research R1, R5).

1. It imports no broker SDK and no trading component, so no path reaches an order.
2. Only the Anthropic adapter imports the `anthropic` SDK.
3. The pure core (selection, prompt, answer) does no I/O and reads no clock.
4. No module names another component's credential.
"""

from __future__ import annotations

import ast
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "research"
PURE = ("selection.py", "prompt.py", "answer.py")
FORBIDDEN_ANYWHERE = (
    "alpaca",
    "openai",
    "trading_agent.execution",
    "trading_agent.orchestrator",
    "trading_agent.risk.service",
    "trading_agent.risk.gate",
    "trading_agent.risk.rules",
    "trading_agent.risk.model",
    "trading_agent.risk.config",
)
FORBIDDEN_IN_PURE = (
    "psycopg",
    "urllib",
    "anthropic",
    "os",
    "time",
    "trading_agent.research.service",
    "trading_agent.research.finnhub",
    "trading_agent.research.qwen",
    "trading_agent.research.anthropic_client",
)
OTHER_COMPONENTS_VARIABLES = (
    "ALPACA_",
    "EXECUTION_DATABASE_URL",
    "RISK_GATE_DATABASE_URL",
    "ADMIN_DATABASE_URL",
    "REFERENCE_DATA_",
    "ORCHESTRATOR_DATABASE_URL",
    "OPPORTUNISTIC_IDENTIFIER_",
    "PORTFOLIO_MANAGER_",
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


def _modules():
    return sorted(RESEARCH.glob("*.py"))


def test_the_package_exists():
    assert (RESEARCH / "__init__.py").exists()


def test_no_broker_sdk_or_trading_component():
    for path in _modules():
        bad = {n for n in _imports(path) for f in FORBIDDEN_ANYWHERE if _matches(n, f)}
        assert not bad, f"{path.name} imports {bad}"


def test_only_the_anthropic_adapter_imports_the_sdk():
    for path in _modules():
        if path.name == "anthropic_client.py":
            continue
        assert not any(_matches(n, "anthropic") for n in _imports(path)), path.name


def test_the_pure_core_does_no_io():
    for name in PURE:
        path = RESEARCH / name
        if not path.exists():
            continue  # built in a later task
        bad = {n for n in _imports(path) for f in FORBIDDEN_IN_PURE if _matches(n, f)}
        assert not bad, f"{name} imports {bad}"
        assert "datetime.now" not in path.read_text(), f"{name} reads the clock"


def test_no_module_names_another_components_credential():
    for path in _modules():
        text = path.read_text()
        for name in OTHER_COMPONENTS_VARIABLES:
            assert name not in text, f"{path.name} mentions {name}"
