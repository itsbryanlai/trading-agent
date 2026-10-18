"""In-process conversation memory (specs/013 T007, research A13)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_agent.assistant.memory import Exchange, Memory

SGT = ZoneInfo("Asia/Singapore")
T0 = datetime(2026, 10, 20, 7, 0, tzinfo=SGT)


def test_keeps_the_newest_n_oldest_first():
    memory = Memory(max_exchanges=2, idle_minutes=30)
    for i in range(3):
        memory.add(T0 + timedelta(minutes=i), f"q{i}", f"r{i}")
    got = memory.recent(T0 + timedelta(minutes=3))
    assert got == [
        Exchange(T0 + timedelta(minutes=1), "q1", "r1"),
        Exchange(T0 + timedelta(minutes=2), "q2", "r2"),
    ]


def test_cleared_after_the_idle_period():
    memory = Memory(max_exchanges=5, idle_minutes=30)
    memory.add(T0, "q", "r")
    assert len(memory.recent(T0 + timedelta(minutes=30))) == 1  # 07:30, not yet older
    assert memory.recent(T0 + timedelta(minutes=31)) == []  # 07:31
    assert memory.recent(T0 + timedelta(minutes=1)) == []  # it stays cleared


def test_a_new_exchange_after_a_gap_starts_fresh():
    memory = Memory(max_exchanges=5, idle_minutes=30)
    memory.add(T0, "old", "r")
    later = T0 + timedelta(minutes=31)
    assert memory.recent(later) == []
    memory.add(later, "new", "r")
    assert [e.question for e in memory.recent(later)] == ["new"]


def test_zero_exchanges_remembers_nothing():
    memory = Memory(max_exchanges=0, idle_minutes=30)
    memory.add(T0, "q", "r")
    assert memory.recent(T0) == []


def test_empty():
    assert Memory(5, 30).recent(T0) == []
