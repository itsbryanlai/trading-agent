"""The fake launcher behaves as the tests rely on (contracts/launcher-port.md)."""

from __future__ import annotations

import pytest

from tests.fakes.launcher import FakeLauncher
from trading_agent.orchestrator.launcher import LaunchFailed


def test_start_poll_finish_stop():
    fake = FakeLauncher()
    a = fake.start("trading_agent.research", {"RESEARCH_X": "v", "PATH": "/bin"})
    b = fake.start("trading_agent.portfolio_manager", {})
    assert a.pgid != b.pgid
    assert fake.poll(a) is None
    fake.finish(a, 1)
    assert fake.poll(a) == 1
    assert fake.stop(b) == -15 and fake.stops == [b.pgid]
    assert fake.starts[0][2] == ("PATH", "RESEARCH_X")
    assert fake.modules_started() == ["trading_agent.research", "trading_agent.portfolio_manager"]


def test_failing_start_and_orphans():
    fake = FakeLauncher()
    fake.fail_start("trading_agent.research")
    with pytest.raises(LaunchFailed):
        fake.start("trading_agent.research", {})
    fake.live_orphans.add(42)
    assert fake.stop_group(42, "m") is True
    assert fake.stop_group(43, "m") is False
    assert fake.group_stops == [(42, "m"), (43, "m")]
