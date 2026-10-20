"""Only control.py names the control login's variable; only three modules import it (T019)."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"
PACKAGE = SRC / "trading_agent" / "dashboard"
VARIABLE = "DASHBOARD_CONTROL_DATABASE_URL"
MAY_IMPORT = {"routes_toggle.py", "check.py", "__main__.py", "control.py"}


def _imports_control(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            if node.module == "trading_agent.dashboard.control":
                return True
            if node.module == "trading_agent.dashboard" and any(
                a.name == "control" for a in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            if any(a.name == "trading_agent.dashboard.control" for a in node.names):
                return True
    return False


def test_the_variable_name_appears_only_in_control_py_under_src():
    holders = {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".sql", ".yaml", ".html", ".css", ".md"}
        and VARIABLE in p.read_text(errors="ignore")
    }
    assert holders == {"trading_agent/dashboard/control.py"}


def test_only_the_toggle_route_check_and_entry_point_import_control():
    importers = {p.name for p in PACKAGE.glob("*.py") if _imports_control(p)}
    assert importers <= MAY_IMPORT, importers - MAY_IMPORT
    assert "control.py" not in importers
    assert "__main__.py" in importers


def test_no_other_package_imports_the_control_writer():
    for path in (SRC / "trading_agent").rglob("*.py"):
        if PACKAGE in path.parents:
            continue
        assert "dashboard.control" not in path.read_text(), path


def test_the_guard_detects_an_import(tmp_path):
    for source in (
        "from trading_agent.dashboard import control\n",
        "from trading_agent.dashboard.control import ControlWriter\n",
        "import trading_agent.dashboard.control\n",
    ):
        sample = tmp_path / "sample.py"
        sample.write_text(source)
        assert _imports_control(sample)
    sample.write_text("from trading_agent.dashboard import store\n")
    assert not _imports_control(sample)
