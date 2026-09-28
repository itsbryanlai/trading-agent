"""Structural guards for Execution (specs/003-execution research E1, E2).

1. Only execution/alpaca.py imports the broker SDK, and nothing outside the
   execution package imports that adapter: one holder of the broker credential
   (Constitution I, III).
2. The pure core does no I/O: no database driver, no SDK, no environment, no
   clock, and no import of the modules that do those things.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src" / "trading_agent"
EXECUTION = SRC / "execution"
ADAPTER = EXECUTION / "alpaca.py"
CORE_MODULES = ("model.py", "ids.py", "checks.py", "fills.py", "monitor.py", "reasons.py")
NO_CLOCK_MODULES = (*CORE_MODULES, "schedule.py")
FORBIDDEN_IN_CORE = {
    "psycopg",
    "alpaca",
    "os",
    "trading_agent.execution.service",
    "trading_agent.execution.alpaca",
    "trading_agent.risk.calendar",
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _hits(names: set[str], prefix: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for name in names)


def test_only_the_adapter_imports_the_broker_sdk():
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path != ADAPTER and _hits(_imports(path), "alpaca")
    ]
    assert offenders == []


def test_nothing_outside_execution_imports_the_adapter():
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if EXECUTION not in path.parents and _hits(_imports(path), "trading_agent.execution.alpaca")
    ]
    assert offenders == []


def test_pure_core_has_no_io_imports():
    for filename in CORE_MODULES:
        path = EXECUTION / filename
        if not path.exists():
            continue
        imported = _imports(path)
        bad = {f for f in FORBIDDEN_IN_CORE if _hits(imported, f)}
        assert not bad, f"{filename} imports {sorted(bad)}"


def test_core_and_schedule_never_read_the_clock():
    for filename in NO_CLOCK_MODULES:
        path = EXECUTION / filename
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            called = isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            if called and node.func.attr in {"now", "today", "utcnow"}:
                raise AssertionError(f"{filename} calls .{node.func.attr}(): the clock is an input")


def test_core_modules_exist_so_the_guard_is_not_vacuous():
    assert all((EXECUTION / name).exists() for name in ("model.py", "ids.py", "checks.py"))


# (owner, attribute) pairs that read the wall clock, called or not.
_CLOCK_READS = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "monotonic"),
}


def _reads_the_clock(node: ast.AST) -> str | None:
    """`datetime.now`, `date.today`, `datetime.datetime.utcnow`, `time.time` and so
    on, called or not (e.g. `clock=datetime.now`). The injected `ctx.now` /
    `session.now` is fine: its owner isn't a clock."""
    if not isinstance(node, ast.Attribute):
        return None
    owner = node.value
    name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", None)
    return f"{name}.{node.attr}" if (name, node.attr) in _CLOCK_READS else None


def test_the_clock_is_never_referenced_in_the_core():
    # Second review F9: an uncalled reference such as default_factory=datetime.now
    # reads the clock just as surely as a call.
    for filename in NO_CLOCK_MODULES:
        path = EXECUTION / filename
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            found = _reads_the_clock(node)
            assert found is None, f"{filename} references {found}: the clock is an input"
