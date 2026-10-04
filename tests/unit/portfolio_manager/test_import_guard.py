"""Structural guards for the Portfolio Manager (specs/008-portfolio-manager FR-019, FR-020).

The PM is an analyst: it can propose decisions and nothing else. So no PM module imports
anything from `execution`, `orchestrator`, `research` or `risk`, except the exchange
calendar (`risk.calendar`, a pure function of the clock). And the PM never reads the gate's
limits: no PM source mentions `risk.yaml` (a PM that knew the limits could size to them).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "portfolio_manager"
MODULES = sorted(PACKAGE.glob("*.py"))
FORBIDDEN_PACKAGES = ("execution", "orchestrator", "research", "risk")
ALLOWED = {"trading_agent.risk.calendar"}


def imported_targets(source: str) -> set[str]:
    """Every dotted name a module imports, `from a import b` counted as `a.b`, so
    `from trading_agent.risk import calendar` is judged as `trading_agent.risk.calendar`."""
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            targets.update(f"{base}.{alias.name}" for alias in node.names)
    return targets


def check(source: str) -> set[str]:
    """The forbidden imports in `source`."""
    return {
        target
        for target in imported_targets(source)
        if not any(target == ok or target.startswith(f"{ok}.") for ok in ALLOWED)
        and any(
            target == f"trading_agent.{pkg}" or target.startswith(f"trading_agent.{pkg}.")
            for pkg in FORBIDDEN_PACKAGES
        )
    }


def test_there_are_modules_to_check():
    assert len(MODULES) >= 8 and PACKAGE / "service.py" in MODULES


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_a_module_imports_nothing_forbidden(module):
    assert check(module.read_text()) == set()


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_module_mentions_the_gates_config_file(module):
    assert "risk.yaml" not in module.read_text()


@pytest.mark.parametrize(
    "line",
    [
        "import trading_agent.execution.broker",
        "from trading_agent.execution import broker",
        "from trading_agent.orchestrator.config import load_config",
        "import trading_agent.research",
        "from trading_agent.research.service import run",
        "from trading_agent.risk.gate import evaluate",
        "from trading_agent.risk.service import evaluate_decision",
        "from trading_agent.risk import gate",
        "from trading_agent.risk import calendar, gate",
    ],
)
def test_the_guard_catches_a_forbidden_import(line):
    assert check(f"{line}\n") != set()


@pytest.mark.parametrize(
    "line",
    [
        "from trading_agent.risk import calendar",
        "from trading_agent.risk.calendar import market_open",
        "import trading_agent.risk.calendar",
        "from trading_agent.llm.ports import ModelClient",
        "from trading_agent.reference.provider import Quote",
    ],
)
def test_the_guard_allows_the_calendar_and_lower_layers(line):
    assert check(f"{line}\n") == set()
