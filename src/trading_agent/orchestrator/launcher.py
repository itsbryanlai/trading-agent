"""Starting and stopping agent processes (contracts/launcher-port.md; research O3).

Each agent runs as `python -m <module>` in a new session, so it leads its own
process group and a timeout can stop the agent and anything it started. The
price is that an agent doesn't die with the orchestrator, so the orchestrator
stops every group on shutdown and reaps orphans after a crash (O12, FR-005a,
FR-023). The environment passed in is complete: nothing else is inherited.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DEFAULT_GRACE_SECONDS = 10.0


class LaunchFailed(Exception):
    """The process couldn't be started. Carries the error type only."""

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


@dataclass
class Handle:
    pgid: int
    process: object = field(default=None, repr=False)


class Launcher(Protocol):
    def start(self, module: str, env: dict[str, str]) -> Handle: ...

    def poll(self, handle: Handle) -> int | None: ...

    def stop(self, handle: Handle) -> int | None: ...

    def stop_group(self, pgid: int, module: str) -> bool: ...


class SubprocessLauncher:
    def __init__(self, grace_seconds: float = DEFAULT_GRACE_SECONDS) -> None:
        self.grace_seconds = grace_seconds

    def start(self, module: str, env: dict[str, str]) -> Handle:
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", module],
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise LaunchFailed(type(exc).__name__) from None
        # A session leader's process-group id is its own pid.
        return Handle(pgid=process.pid, process=process)

    def poll(self, handle: Handle) -> int | None:
        return handle.process.poll()

    def stop(self, handle: Handle) -> int | None:
        """SIGTERM the whole group, SIGKILL whatever is left after the grace period."""
        _signal_group(handle.pgid, signal.SIGTERM)
        try:
            status = handle.process.wait(timeout=self.grace_seconds)
        except subprocess.TimeoutExpired:
            _signal_group(handle.pgid, signal.SIGKILL)
            status = handle.process.wait()
        # The leader has gone; anything it started that ignored SIGTERM hasn't.
        _signal_group(handle.pgid, signal.SIGKILL)
        return status

    def stop_group(self, pgid: int, module: str) -> bool:
        """Stop an orphan left by a crashed orchestrator (research O12).

        Only if the group is alive and its leader is still running `module`: a
        reused process id must never lead to signalling an unrelated process.
        """
        if not _group_alive(pgid):
            return False
        command = _command_line(pgid)
        if command is None or module not in command:
            return False
        _signal_group(pgid, signal.SIGTERM)
        deadline = time.monotonic() + self.grace_seconds
        while time.monotonic() < deadline and _group_alive(pgid):
            time.sleep(0.05)
        _signal_group(pgid, signal.SIGKILL)
        return True


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _command_line(pid: int) -> str | None:
    proc = Path(f"/proc/{pid}/cmdline")
    if proc.exists():
        try:
            return proc.read_bytes().replace(b"\0", b" ").decode(errors="replace").strip() or None
        except OSError:
            return None
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": os.environ.get("PATH", "/bin:/usr/bin")},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None
