"""Structural guards (specs/002-risk-gate research G1, G15).

1. Only trading_agent.risk may import the risk-config loader: the Portfolio
   Manager, in particular, must never read the limits it is judged against.
2. The pure core does no I/O: no database driver, no calendar, no environment,
   no clock, and no import of the service that does those things.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src" / "trading_agent"
RISK = SRC / "risk"
CORE_MODULES = ("gate.py", "model.py", "rules.py", "config.py")
FORBIDDEN_IN_CORE = {
    "psycopg",
    "exchange_calendars",
    "os",
    "trading_agent.risk.service",
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


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC.parent).with_suffix("").parts)


def test_only_the_risk_package_imports_the_config_loader():
    offenders = [
        _module_name(path)
        for path in SRC.rglob("*.py")
        if RISK not in path.parents
        and any(name.startswith("trading_agent.risk.config") for name in _imports(path))
    ]
    assert offenders == []


def test_pure_core_has_no_io_imports():
    for filename in CORE_MODULES:
        path = RISK / filename
        if not path.exists():
            continue
        imported = _imports(path)
        bad = {
            name
            for name in imported
            for forbidden in FORBIDDEN_IN_CORE
            if name == forbidden or name.startswith(forbidden + ".")
        }
        assert not bad, f"{filename} imports {sorted(bad)}"


def test_pure_core_never_reads_the_clock():
    for filename in CORE_MODULES:
        path = RISK / filename
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in {"now", "today", "utcnow"}:
                raise AssertionError(f"{filename} calls .{node.attr}() — the clock is an input")


def test_core_modules_exist_so_the_guard_is_not_vacuous():
    assert all((RISK / name).exists() for name in ("model.py", "rules.py", "config.py"))
