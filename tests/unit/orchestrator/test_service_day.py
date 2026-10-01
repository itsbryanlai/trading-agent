"""The service with a fake launcher and an in-memory store (US1, US4; FR-004, FR-005,
FR-005a, FR-008a, FR-019, FR-023, FR-023a; SC-001, SC-002, SC-005)."""

from __future__ import annotations

from datetime import timedelta

from tests.fakes.launcher import FakeLauncher
from tests.unit.orchestrator.support import SATURDAY, MemoryStore, config, et
from trading_agent.orchestrator.service import BASE_ENVIRONMENT, Orchestrator

FAKE_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/home/agent",
    "RESEARCH_TOKEN": "fake-research-value",
    "PORTFOLIO_MANAGER_TOKEN": "fake-pm-value",
    "EXECUTION_SECRET": "must-never-reach-an-agent",
}


class Clock:
    def __init__(self, now):
        self.now = now


def make(cfg=None, env=FAKE_ENV):
    cfg = cfg or config(
        research={"env": ("RESEARCH_TOKEN",)},
        portfolio_manager={"env": ("PORTFOLIO_MANAGER_TOKEN",)},
    )
    clock = Clock(None)
    launcher = FakeLauncher(clock=lambda: clock.now)
    store = MemoryStore()
    orch = Orchestrator(cfg, store, launcher, env.get)
    return orch, launcher, store, clock


def run_day(orch, launcher, clock, start, end, run_for=timedelta(minutes=2)):
    started_at = {}
    now = start
    while now <= end:
        clock.now = now
        for pgid, when in list(started_at.items()):
            if now >= when + run_for:
                from trading_agent.orchestrator.launcher import Handle

                launcher.finish(Handle(pgid), 0)
                del started_at[pgid]
        before = len(launcher.starts)
        orch.tick(now)
        for i in range(before, len(launcher.starts)):
            started_at[5000 + i] = now
        now += timedelta(seconds=30)


def test_a_simulated_day_starts_every_slot_within_a_minute():
    orch, launcher, store, clock = make()
    run_day(orch, launcher, clock, et("07:00"), et("17:00"))
    started = sorted((when, module) for when, module, _ in launcher.starts)
    expected = sorted(
        [(et("08:30"), "trading_agent.research"), (et("10:00"), "trading_agent.portfolio_manager")]
        + [(et(f"{h}:00"), "trading_agent.opportunistic_identifier") for h in range(10, 16)]
    )
    assert [m for _, m in started] == [m for _, m in expected]
    for (got, _), (want, _) in zip(started, expected, strict=True):
        assert timedelta(0) <= got - want < timedelta(minutes=1)
    assert all(r.outcome == "succeeded" for r in store.records.values())


def test_each_agent_gets_only_its_own_names_and_the_base_set():
    orch, launcher, store, clock = make()
    run_day(orch, launcher, clock, et("08:29"), et("10:01"))
    by_module = {module: names for _, module, names in launcher.starts}
    assert by_module["trading_agent.research"] == ("HOME", "PATH", "RESEARCH_TOKEN")
    assert by_module["trading_agent.portfolio_manager"] == (
        "HOME",
        "PATH",
        "PORTFOLIO_MANAGER_TOKEN",
    )
    assert by_module["trading_agent.opportunistic_identifier"] == ("HOME", "PATH")
    for names in by_module.values():
        assert "EXECUTION_SECRET" not in names
        assert set(names) <= set(BASE_ENVIRONMENT) | {"RESEARCH_TOKEN", "PORTFOLIO_MANAGER_TOKEN"}


def test_a_weekend_creates_no_records():
    orch, launcher, store, clock = make()
    run_day(orch, launcher, clock, et("07:00", SATURDAY), et("17:00", SATURDAY))
    assert store.records == {} and launcher.starts == []


def test_the_record_is_written_before_the_process_starts():
    orch, launcher, store, clock = make(cfg=config(enabled=("research",)))
    order = []
    original_insert, original_start = store.insert_start, launcher.start

    def insert(*args):
        order.append("record")
        return original_insert(*args)

    def start(*args):
        order.append("process")
        return original_start(*args)

    store.insert_start, launcher.start = insert, start
    orch.tick(et("08:30"))
    assert order == ["record", "process"]
    (row,) = store.records.values()
    assert row.pgid == 5000 and row.outcome == "running"


def test_a_failed_launch_is_recorded_and_not_retried():
    orch, launcher, store, clock = make(cfg=config(enabled=("research",)))
    launcher.fail_start("trading_agent.research")
    orch.tick(et("08:30"))
    orch.tick(et("08:31"))
    orch.tick(et("09:00"))
    (row,) = store.records.values()
    assert row.outcome == "failed"
    assert store.details[row.id] == "launch failed: FileNotFoundError"
    assert launcher.starts == []


def test_a_hung_agent_is_stopped_and_the_others_start_on_time():
    orch, launcher, store, clock = make()
    # Research at 08:30 finishes; the Identifier hangs from 11:00.
    run_day(orch, launcher, clock, et("08:29"), et("10:59"))
    clock.now = et("11:00")
    orch.tick(clock.now)
    hung = launcher.running("trading_agent.opportunistic_identifier")
    now = et("11:00")
    while now <= et("11:15"):
        clock.now = now
        orch.tick(now)
        now += timedelta(seconds=30)
    assert hung.pgid in launcher.stops
    (oi_11,) = [r for r in store.records.values() if r.slot_key == "oi@11:00"]
    assert oi_11.outcome == "timed_out"
    assert oi_11.finished_at - et("11:10") < timedelta(minutes=1)


def test_shutdown_stops_every_running_agent_and_records_it():
    orch, launcher, store, clock = make()
    clock.now = et("10:00")
    orch.tick(et("08:30"))
    orch.tick(et("10:00"))
    running = [r for r in store.records.values() if r.outcome == "running"]
    assert running
    before = len(launcher.stops)
    orch.shutdown(et("10:01"))
    assert sorted(launcher.stops[before:]) == sorted(r.pgid for r in running)
    assert all(store.records[r.id].outcome == "interrupted" for r in running)


def test_startup_reaps_orphans_then_marks_them_interrupted():
    orch, launcher, store, clock = make()
    orch.tick(et("08:30"))
    orch.tick(et("10:00"))
    rows = [r for r in store.records.values() if r.outcome == "running"]
    # A new process: fresh memory, same records. One orphan is still alive.
    fresh = Orchestrator(orch.cfg, store, FakeLauncher(), FAKE_ENV.get)
    fresh.launcher.live_orphans.add(rows[0].pgid)
    fresh.startup(et("10:05"))
    assert {pgid for pgid, _ in fresh.launcher.group_stops} == {r.pgid for r in rows}
    assert all(store.records[r.id].outcome == "interrupted" for r in rows)
