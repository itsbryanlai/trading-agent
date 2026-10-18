"""Structural guard for the Assistant (specs/013 tasks T001; research A19).

Imports are limited to the standard library, PyYAML, the allowed project modules and the
Assistant itself. The pure modules do no I/O, never import the modules that do, and read no
clock (times are arguments).
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "assistant"
PURE = ("inbox.py", "actions.py", "menu.py", "links.py", "memory.py", "replies.py",
        "prompt.py", "answer.py")
ALLOWED_PROJECT = (
    "trading_agent.assistant",
    "trading_agent.llm",
    "trading_agent.storage.db",
    "trading_agent.no_redirect",
)
FORBIDDEN_IN_PURE = (
    "psycopg",
    "urllib",
    "os",
    "signal",
    "trading_agent.assistant.telegram",
    "trading_agent.assistant.store",
    "trading_agent.assistant.loop",
    "trading_agent.assistant.service",
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


def test_no_sibling_component_risk_or_reference_is_imported():
    forbidden = (
        "trading_agent.research",
        "trading_agent.portfolio_manager",
        "trading_agent.orchestrator",
        "trading_agent.opportunistic_identifier",
        "trading_agent.journal",
        "trading_agent.execution",
        "trading_agent.risk",
        "trading_agent.reference",
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
    bad.write_text(
        "import psycopg\nimport os\nimport signal\nfrom trading_agent.risk import gate\n"
        "from trading_agent.assistant import telegram\n"
    )
    names = _imports(bad)
    assert any(_matches(n, "psycopg") for n in names)
    assert any(_matches(n, "os") for n in names)
    assert any(_matches(n, "signal") for n in names)
    assert any(_matches(n, "trading_agent.assistant.telegram") for n in names)
    assert _outside_the_list(names) == {"trading_agent.risk.gate", "trading_agent.risk"}
