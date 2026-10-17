"""Structural guards for the Opportunistic Identifier (specs/011 plan; research O1, O14).

1. It imports no sibling agent, no broker SDK and no trading component, so no path reaches
   an order, a portfolio or a decision.
2. Only the Anthropic adapter imports the `anthropic` SDK.
3. The pure core (rotation, screen, prompt, answer, text, outcome) does no I/O: no database
   driver, no HTTP, no environment, no clock, and no import of the modules that do.
4. No module names another component's credential, or reads an unprefixed variable.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "opportunistic_identifier"
PURE = ("rotation.py", "screen.py", "prompt.py", "answer.py", "text.py", "outcome.py")
# The siblings the layering forbids (also enforced by lint-imports), the broker, and the
# Risk Gate's own modules: the agent reads the gate's rule names and universe floors only.
FORBIDDEN_ANYWHERE = (
    "alpaca",
    "openai",
    "trading_agent.research",
    "trading_agent.portfolio_manager",
    "trading_agent.orchestrator",
    "trading_agent.execution",
    "trading_agent.risk.service",
    "trading_agent.risk.gate",
    "trading_agent.risk.model",
    "trading_agent.risk.runner",
)
FORBIDDEN_IN_PURE = (
    "psycopg",
    "urllib",
    "http",
    "socket",
    "anthropic",
    "os",
    "time",
    "random",
    "trading_agent.opportunistic_identifier.service",
    "trading_agent.opportunistic_identifier.finnhub",
    "trading_agent.opportunistic_identifier.fetch",
    "trading_agent.opportunistic_identifier.check",
    "trading_agent.opportunistic_identifier.dry_run",
    "trading_agent.llm",
    "trading_agent.storage",
)
OTHER_COMPONENTS_VARIABLES = (
    "ALPACA_",
    "EXECUTION_DATABASE_URL",
    "RISK_GATE_DATABASE_URL",
    "ADMIN_DATABASE_URL",
    "REFERENCE_DATA_",
    "ORCHESTRATOR_DATABASE_URL",
    "RESEARCH_",
    "PORTFOLIO_MANAGER_",
)
# Names a module may only use with this agent's prefix (FR-020).
PREFIXED = ("DATABASE_URL", "FINNHUB_API_KEY", "DASHSCOPE_API_KEY", "QWEN_BASE_URL")
UNPREFIXED = re.compile(
    r"(?<!OPPORTUNISTIC_IDENTIFIER_)(?<![A-Z_])(ANTHROPIC_API_KEY|"
    + "|".join(PREFIXED)
    + r")(?![A-Z_])"
)
CLOCK_READS = ("datetime.now", "datetime.utcnow", "date.today", "time.time", "time.monotonic")


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _matches(name: str, forbidden: str) -> bool:
    return name == forbidden or name.startswith(forbidden + ".")


def _modules() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def test_the_package_and_every_pure_module_exist_so_the_guards_are_not_vacuous():
    assert (PACKAGE / "__init__.py").exists()
    assert all((PACKAGE / name).exists() for name in PURE)


def test_no_sibling_agent_broker_or_trading_component_is_imported():
    for path in _modules():
        bad = {n for n in _imports(path) for f in FORBIDDEN_ANYWHERE if _matches(n, f)}
        assert not bad, f"{path.name} imports {sorted(bad)}"


def test_only_the_gates_rule_names_and_the_loader_are_read_from_the_risk_package():
    allowed = {
        "trading_agent.risk.calendar",
        "trading_agent.risk.rules",
        "trading_agent.risk.config",
    }
    for path in _modules():
        risk = {n for n in _imports(path) if _matches(n, "trading_agent.risk")}
        risk -= {"trading_agent.risk"}  # `from trading_agent.risk import calendar, rules`
        extra = {n for n in risk if not any(_matches(n, a) for a in allowed)}
        assert not extra, f"{path.name} imports {sorted(extra)}"


def test_only_the_anthropic_adapter_imports_the_sdk():
    for path in _modules():
        assert not any(_matches(n, "anthropic") for n in _imports(path)), path.name


def test_the_pure_core_does_no_io_and_reads_no_clock():
    for name in PURE:
        path = PACKAGE / name
        bad = {n for n in _imports(path) for f in FORBIDDEN_IN_PURE if _matches(n, f)}
        assert not bad, f"{name} imports {sorted(bad)}"
        source = path.read_text()
        for read in CLOCK_READS:
            assert read not in source, f"{name} reads the clock ({read})"


def test_no_module_imports_the_environment_directly():
    # Variables are read only through storage.db.require_env, in __main__.
    for path in _modules():
        assert not any(_matches(n, "os") for n in _imports(path)), path.name


def test_no_module_names_another_components_credential():
    for path in _modules():
        text = path.read_text()
        for name in OTHER_COMPONENTS_VARIABLES:
            assert name not in text, f"{path.name} mentions {name}"


def test_every_variable_it_reads_carries_its_own_prefix():
    for path in _modules():
        found = UNPREFIXED.findall(path.read_text())
        assert not found, f"{path.name} names an unprefixed variable: {found}"


def test_no_model_endpoint_is_in_the_source():
    """Qwen's endpoint comes from OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL (FR-010)."""
    for path in _modules():
        text = path.read_text()
        assert "qwencloudapi" not in text and "dashscope.aliyuncs" not in text, path.name


def test_the_guards_catch_what_they_guard_against(tmp_path):
    """A sanity check on the checks themselves."""
    bad = tmp_path / "bad.py"
    bad.write_text("import psycopg\nfrom trading_agent.research import service\nimport os\n")
    names = _imports(bad)
    assert any(_matches(n, "psycopg") for n in names)
    assert any(_matches(n, "trading_agent.research") for n in names)
    assert UNPREFIXED.findall("x = 'ANTHROPIC_API_KEY' + 'DATABASE_URL'") == [
        "ANTHROPIC_API_KEY",
        "DATABASE_URL",
    ]
    assert UNPREFIXED.findall("OPPORTUNISTIC_IDENTIFIER_DATABASE_URL") == []
