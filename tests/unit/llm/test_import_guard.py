"""Structural guards for trading_agent.llm (specs/008-portfolio-manager research P5;
carried over from research/test_import_guard.py for the moved code).

1. Only the Anthropic adapter imports the `anthropic` SDK.
2. No llm module imports a component: the package sits at the bottom of the layering.
3. No llm module names another component's credential variable.
4. No model endpoint is in the source.
"""

from __future__ import annotations

import ast
from pathlib import Path

LLM = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "llm"
COMPONENTS = (
    "trading_agent.research",
    "trading_agent.portfolio_manager",
    "trading_agent.execution",
    "trading_agent.orchestrator",
    "trading_agent.risk",
    "trading_agent.reference",
    "trading_agent.storage",
)
CREDENTIAL_PREFIXES = (
    "RESEARCH_",
    "PORTFOLIO_MANAGER_",
    "OPPORTUNISTIC_IDENTIFIER_",
    "REFERENCE_DATA_",
    "ALPACA_",
    "EXECUTION_DATABASE_URL",
    "RISK_GATE_DATABASE_URL",
    "ADMIN_DATABASE_URL",
    "ORCHESTRATOR_DATABASE_URL",
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
    return sorted(LLM.glob("*.py"))


def test_the_package_and_its_modules_exist():
    names = {path.name for path in _modules()}
    assert {
        "__init__.py",
        "ports.py",
        "settings.py",
        "qwen.py",
        "anthropic_client.py",
    } <= names


def test_only_the_anthropic_adapter_imports_the_sdk():
    importers = {
        path.name
        for path in _modules()
        if any(_matches(name, "anthropic") for name in _imports(path))
    }
    assert importers == {"anthropic_client.py"}


def test_no_llm_module_imports_a_component():
    for path in _modules():
        bad = {n for n in _imports(path) for c in COMPONENTS if _matches(n, c)}
        assert not bad, f"{path.name} imports {bad}"


def test_no_llm_module_names_another_components_credential():
    for path in _modules():
        text = path.read_text()
        for name in CREDENTIAL_PREFIXES:
            assert name not in text, f"{path.name} mentions {name}"


def test_no_model_endpoint_is_in_the_source():
    for path in _modules():
        assert "qwencloudapi" not in path.read_text(), path.name
