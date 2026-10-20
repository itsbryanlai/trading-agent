"""Structural guard for the dashboard (specs/014-dashboard tasks T001; research D3).

Imports from other `trading_agent` packages are limited to `storage.db` and `risk.calendar`
(never a sibling top-layer package). The pure modules do no I/O, never import the modules that
do, and read no clock or environment: times and values are arguments.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "src" / "trading_agent" / "dashboard"
PURE_NAMES = ("auth", "throttle", "toggle", "render", "timefmt", "charts", "attribution")
ALLOWED_PROJECT = (
    "trading_agent.dashboard",
    "trading_agent.storage.db",
    "trading_agent.risk.calendar",
)
FORBIDDEN_IN_PURE = (
    "psycopg",
    "fastapi",
    "starlette",
    "os",
    "trading_agent.dashboard.store",
    "trading_agent.dashboard.control",
    "trading_agent.dashboard.deployed",
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
        elif isinstance(node, ast.ImportFrom) and node.level:
            raise AssertionError(f"{path.name}: relative imports are not used here")
    return names


def _matches(name: str, other: str) -> bool:
    return name == other or name.startswith(other + ".")


def _modules() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def _pure_modules() -> list[Path]:
    return [
        path
        for path in _modules()
        if path.stem in PURE_NAMES or path.stem == "pages" or path.stem.startswith("pages_")
    ]


def _clock_reads(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            parts = []
            target = node.func
            while isinstance(target, ast.Attribute):
                parts.append(target.attr)
                target = target.value
            if isinstance(target, ast.Name):
                parts.append(target.id)
            dotted = ".".join(reversed(parts))
            if any(dotted == read or dotted.endswith("." + read) for read in CLOCK_READS):
                found.append(dotted)
    return found


def test_only_allowed_project_imports():
    for path in _modules():
        for name in _imports(path):
            if name.startswith("trading_agent"):
                assert any(_matches(name, ok) or _matches(ok, name) for ok in ALLOWED_PROJECT), (
                    f"{path.name} imports {name}"
                )


def test_pure_modules_do_no_io_and_read_no_clock():
    for path in _pure_modules():
        for name in _imports(path):
            for forbidden in FORBIDDEN_IN_PURE:
                assert not _matches(name, forbidden), f"{path.name} imports {name}"
        assert not _clock_reads(path), f"{path.name} reads the clock: {_clock_reads(path)}"


def test_detects_a_clock_read(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("import datetime\nx = datetime.datetime.now()\n")
    assert _clock_reads(sample)
