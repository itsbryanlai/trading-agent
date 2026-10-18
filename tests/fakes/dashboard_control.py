"""A fake control writer: records calls, can fail, can mirror a write into a FakeStore (T019)."""

from __future__ import annotations

from trading_agent.dashboard.control import ControlError


class FakeControl:
    def __init__(
        self,
        *,
        result: str = "written",
        error: Exception | None = None,
        store=None,
        memberships=None,
        membership_error=None,
    ):
        self.result = result
        self.error = error
        self.store = store
        self.membership_result = memberships or (
            {
                "login": "ta_dashboard_control_login",
                "rolsuper": False,
                "rolcreaterole": False,
                "rolcreatedb": False,
                "rolbypassrls": False,
            },
            ["ta_dashboard_control"],
        )
        self.membership_error = membership_error
        self.calls: list[tuple[bool, bool]] = []

    def memberships(self):
        if self.membership_error is not None:
            raise self.membership_error
        return self.membership_result

    def set_paused(self, expected: bool, target: bool) -> str:
        self.calls.append((expected, target))
        if self.error is not None:
            raise self.error
        if self.result == "written" and self.store is not None:
            self.store.state["trading_paused"] = target
        return self.result


def failing() -> FakeControl:
    return FakeControl(error=ControlError("database: OperationalError"))
