"""Structural guard for the journal writer (specs/012 tasks T001; research J13).

Imports are limited to the standard library, the allowed project modules and PyYAML. The five
pure modules do no I/O, read no clock and never import the modules that do.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "journal"
PURE = ("books.py", "usage.py", "facts.py", "summary.py", "state.py")
ALLOWED_PROJECT = (
    "trading_agent.journal",
    "trading_agent.risk.calendar",
    "trading_agent.reference.finnhub",
    "trading_agent.reference.provider",
    "trading_agent.storage.db",
)
FORBIDDEN_IN_PURE = (
    "psycopg",
    "urllib",
    "os",
    "trading_agent.journal.prices",
    "trading_agent.journal.store",
    "trading_agent.journal.service",
)
CLOCK_READS = ("datetime.now", "datetime.utcnow", "date.today", "time.time", "time.monotonic")


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _matches(name: str, other: str) -> bool:
    return name == other or name.startswith(other + ".")


def _modules() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def _outside_the_list(names: set[str]) -> set[str]:
    """Project imports that are neither allowed nor a parent package of an allowed one."""
    bad = set()
    for name in names:
        if not _matches(name, "trading_agent"):
            continue
        if any(_matches(name, a) or _matches(a, name) for a in ALLOWED_PROJECT):
            continue
        bad.add(name)
    return bad


def test_the_package_exists():
    assert (PACKAGE / "__init__.py").exists()


def test_only_the_allowed_project_modules_are_imported():
    for path in _modules():
        bad = _outside_the_list(_imports(path))
        assert not bad, f"{path.name} imports {sorted(bad)}"


def test_no_sibling_llm_gate_rules_or_execution_is_imported():
    forbidden = (
        "trading_agent.research",
        "trading_agent.portfolio_manager",
        "trading_agent.orchestrator",
        "trading_agent.opportunistic_identifier",
        "trading_agent.execution",
        "trading_agent.llm",
        "trading_agent.risk.gate",
        "trading_agent.risk.rules",
    )
    for path in _modules():
        bad = {n for n in _imports(path) for f in forbidden if _matches(n, f)}
        assert not bad, f"{path.name} imports {sorted(bad)}"


def test_the_pure_modules_do_no_io_and_read_no_clock():
    for path in _modules():
        if path.name not in PURE:
            continue
        bad = {n for n in _imports(path) for f in FORBIDDEN_IN_PURE if _matches(n, f)}
        assert not bad, f"{path.name} imports {sorted(bad)}"
        source = path.read_text()
        for read in CLOCK_READS:
            assert read not in source, f"{path.name} reads the clock ({read})"


def test_the_guards_catch_what_they_guard_against(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import psycopg\nimport os\nfrom trading_agent.risk import gate\n")
    names = _imports(bad)
    assert any(_matches(n, "psycopg") for n in names)
    assert any(_matches(n, "os") for n in names)
    assert _outside_the_list(names) == {"trading_agent.risk.gate"}
