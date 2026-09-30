"""The real launcher, with stand-in child processes (research O3, O12; FR-004, FR-005).

Every child is `tests/fakes/agent.py`: no model calls, no network. Each test
builds the child's whole environment itself, with obviously fake values.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from trading_agent.orchestrator.launcher import LaunchFailed, SubprocessLauncher

REPO = Path(__file__).resolve().parents[3]
AGENT = "tests.fakes.agent"
GRACE = 0.5


def _env(mode: str, out: Path | None = None, **extra) -> dict[str, str]:
    env = {"PYTHONPATH": str(REPO), "FAKE_AGENT_MODE": mode, "PATH": "/usr/bin:/bin"}
    if out is not None:
        env["FAKE_AGENT_OUT"] = str(out)
    env.update(extra)
    return env


def _wait_for(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{path} never written")


def _wait_exit(launcher, handle, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = launcher.poll(handle)
        if status is not None:
            return status
        time.sleep(0.05)
    raise AssertionError("child never exited")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers; check it isn't one.
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
        return waited == 0
    except ChildProcessError:
        return _not_zombie(pid)


def _not_zombie(pid: int) -> bool:
    import subprocess

    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


@pytest.fixture
def launcher():
    return SubprocessLauncher(grace_seconds=GRACE)


def test_exit_statuses(launcher):
    ok = launcher.start(AGENT, _env("ok"))
    fail = launcher.start(AGENT, _env("fail"))
    assert _wait_exit(launcher, ok) == 0
    assert _wait_exit(launcher, fail) == 1


def test_the_child_sees_exactly_the_given_names(launcher, tmp_path):
    out = tmp_path / "names"
    handle = launcher.start(AGENT, _env("names", out, RESEARCH_TOKEN="fake-value-not-real"))
    assert _wait_exit(launcher, handle) == 0
    names = set(out.read_text().split("\n"))
    # The interpreter may add a few of its own (e.g. LC_CTYPE on macOS); nothing else.
    assert {"PYTHONPATH", "FAKE_AGENT_MODE", "FAKE_AGENT_OUT", "PATH", "RESEARCH_TOKEN"} <= names
    assert (
        names
        - {
            "PYTHONPATH",
            "FAKE_AGENT_MODE",
            "FAKE_AGENT_OUT",
            "PATH",
            "RESEARCH_TOKEN",
            "LC_CTYPE",
            "__CF_USER_TEXT_ENCODING",
        }
        == set()
    )


def test_stop_ends_a_hung_child_within_the_grace_period(launcher, tmp_path):
    out = tmp_path / "pid"
    handle = launcher.start(AGENT, _env("hang", out))
    pid = int(_wait_for(out))
    started = time.monotonic()
    launcher.stop(handle)
    assert time.monotonic() - started < GRACE + 1
    assert not _alive(pid)


def test_stop_kills_a_child_that_ignores_sigterm(launcher, tmp_path):
    out = tmp_path / "pid"
    handle = launcher.start(AGENT, _env("ignore", out))
    pid = int(_wait_for(out))
    launcher.stop(handle)
    assert not _alive(pid)


def test_stop_kills_the_whole_process_group(launcher, tmp_path):
    out = tmp_path / "grandchild"
    handle = launcher.start(AGENT, _env("spawn", out))
    grandchild = int(_wait_for(out))
    assert _not_zombie(grandchild)
    launcher.stop(handle)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _not_zombie(grandchild):
        time.sleep(0.05)
    assert not _not_zombie(grandchild)


def test_a_missing_module_fails(launcher):
    handle = launcher.start("tests.fakes.no_such_agent", _env("ok"))
    assert _wait_exit(launcher, handle) != 0


def test_an_unstartable_interpreter_is_launch_failed(monkeypatch, launcher):
    monkeypatch.setattr("sys.executable", "/nonexistent/python")
    with pytest.raises(LaunchFailed) as caught:
        launcher.start(AGENT, _env("ok"))
    assert caught.value.error_type == "FileNotFoundError"


def test_stop_group_stops_a_live_orphan_running_the_module(launcher, tmp_path):
    out = tmp_path / "pid"
    handle = launcher.start(AGENT, _env("hang", out))
    pid = int(_wait_for(out))
    assert launcher.stop_group(handle.pgid, "tests.fakes.agent") is True
    _wait_exit(launcher, handle)
    assert not _alive(pid)


def test_stop_group_leaves_a_group_running_something_else(launcher, tmp_path):
    out = tmp_path / "pid"
    handle = launcher.start(AGENT, _env("hang", out))
    _wait_for(out)
    try:
        assert launcher.stop_group(handle.pgid, "trading_agent.portfolio_manager") is False
        assert launcher.poll(handle) is None
    finally:
        launcher.stop(handle)


def test_stop_group_on_a_gone_group(launcher):
    handle = launcher.start(AGENT, _env("ok"))
    _wait_exit(launcher, handle)
    assert launcher.stop_group(handle.pgid, AGENT) is False


def test_stop_kills_a_grandchild_that_ignores_sigterm(launcher, tmp_path):
    # The leader exits on SIGTERM; only the final SIGKILL to the group ends the
    # stubborn grandchild (research O3).
    out = tmp_path / "grandchild"
    handle = launcher.start(AGENT, _env("spawn_stubborn", out))
    grandchild = int(_wait_for(out))
    time.sleep(0.3)  # let the grandchild install its SIGTERM handler
    launcher.stop(handle)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _not_zombie(grandchild):
        time.sleep(0.05)
    assert not _not_zombie(grandchild)
