"""A scripted stand-in for the orchestrator's launcher. No process is ever started.

Records every start with its module and the *names* in its environment, lets a
test finish or fail any handle, and scripts which orphaned groups `stop_group`
finds alive.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from trading_agent.orchestrator.launcher import Handle, LaunchFailed


class FakeLauncher:
    def __init__(self, clock: Callable[[], datetime | None] | None = None) -> None:
        self._clock = clock or (lambda: None)
        self._next_pgid = 5000
        # (clock time, module, sorted variable names)
        self.starts: list[tuple[datetime | None, str, tuple[str, ...]]] = []
        self.stops: list[int] = []
        self.group_stops: list[tuple[int, str]] = []
        self._status: dict[int, int | None] = {}
        self._module: dict[int, str] = {}
        self._fail_modules: set[str] = set()
        self.live_orphans: set[int] = set()

    # --- scripting -------------------------------------------------------------

    def fail_start(self, module: str) -> None:
        self._fail_modules.add(module)

    def finish(self, handle: Handle, status: int = 0) -> None:
        self._status[handle.pgid] = status

    def running(self, module: str) -> Handle:
        for pgid, status in self._status.items():
            if status is None and self._module[pgid] == module:
                return Handle(pgid)
        raise AssertionError(f"no running {module}")

    def finish_all(self, status: int = 0) -> None:
        for pgid, current in list(self._status.items()):
            if current is None:
                self._status[pgid] = status

    def modules_started(self) -> list[str]:
        return [module for _, module, _ in self.starts]

    # --- the port ----------------------------------------------------------------

    def start(self, module: str, env: dict[str, str]) -> Handle:
        if module in self._fail_modules:
            raise LaunchFailed("FileNotFoundError")
        pgid = self._next_pgid
        self._next_pgid += 1
        self.starts.append((self._clock(), module, tuple(sorted(env))))
        self._status[pgid] = None
        self._module[pgid] = module
        return Handle(pgid)

    def poll(self, handle: Handle) -> int | None:
        return self._status.get(handle.pgid)

    def stop(self, handle: Handle) -> int | None:
        self.stops.append(handle.pgid)
        self._status[handle.pgid] = -15
        return -15

    def stop_group(self, pgid: int, module: str) -> bool:
        self.group_stops.append((pgid, module))
        return pgid in self.live_orphans
