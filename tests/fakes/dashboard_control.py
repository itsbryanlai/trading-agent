"""A fake control writer: records calls, can fail, can mirror a write into a FakeStore (T019)."""

from __future__ import annotations

from trading_agent.dashboard.control import ControlError


class FakeControl:
    def __init__(self, *, result: str = "written", error: Exception | None = None, store=None):
        self.result = result
        self.error = error
        self.store = store
        self.calls: list[tuple[bool, bool]] = []

    def set_paused(self, expected: bool, target: bool) -> str:
        self.calls.append((expected, target))
        if self.error is not None:
            raise self.error
        if self.result == "written" and self.store is not None:
            self.store.state["trading_paused"] = target
        return self.result


def failing() -> FakeControl:
    return FakeControl(error=ControlError("database: OperationalError"))
