"""US4 planner rules: catch-up after downtime, Research first, no backfill, and
restart at any time (FR-020, clarification 4, SC-006)."""

from __future__ import annotations

import dataclasses
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.orchestrator.support import Simulator, config, et, record, state
from trading_agent.orchestrator import planner as p

PM = "portfolio_manager"
OI = "opportunistic_identifier"


def test_first_tick_at_1115_catches_up_research_and_the_current_identifier_slot():
    actions = p.plan(et("11:15"), config(), state())
    assert actions == [
        p.Start("research", "catch_up", "research_daily", et("08:30")),
        p.Start(OI, "scheduled", "oi@11:00", et("11:00")),
    ]


def test_the_morning_catch_up_waits_for_research_then_runs():
    running = record(
        "research",
        reason="catch_up",
        slot_key="research_daily",
        slot_at=et("08:30"),
        started=et("11:15"),
    )
    assert not [a for a in p.plan(et("11:20"), config(), state([running])) if a.agent == PM]
    done = dataclasses.replace(running, finished_at=et("11:25"), outcome="succeeded")
    pm = [a for a in p.plan(et("11:25"), config(), state([done])) if a.agent == PM]
    assert pm == [p.Start(PM, "catch_up", "morning_session", et("10:00"))]


def test_a_timed_out_research_still_lets_the_morning_session_run():
    timed_out = record(
        "research",
        reason="catch_up",
        slot_key="research_daily",
        slot_at=et("08:30"),
        started=et("11:15"),
        finished=et("11:30"),
        outcome="timed_out",
    )
    pm = [a for a in p.plan(et("11:30"), config(), state([timed_out])) if a.agent == PM]
    assert pm == [p.Start(PM, "catch_up", "morning_session", et("10:00"))]


def test_no_catch_up_after_the_cutoff():
    actions = p.plan(et("15:31"), config(), state())
    assert not [a for a in actions if a.agent in ("research", PM)]


def test_a_restart_after_the_morning_session_doesnt_repeat_it():
    records = [
        record(
            "research",
            slot_key="research_daily",
            slot_at=et("08:30"),
            started=et("08:30"),
            finished=et("08:40"),
        ),
        record(
            PM,
            reason="morning_session",
            slot_key="morning_session",
            slot_at=et("10:00"),
            started=et("10:00"),
            finished=et("10:05"),
        ),
    ]
    actions = p.plan(et("10:20"), config(), state(records))
    assert not [a for a in actions if a.agent in ("research", PM)]


def test_identifier_slots_are_never_backfilled():
    actions = p.plan(et("13:59"), config(enabled=(OI,)), state())
    assert actions == [p.Start(OI, "scheduled", "oi@13:00", et("13:00"))]
    assert p.plan(et("16:10"), config(enabled=(OI,)), state()) == []


def test_intraday_research_slots_are_never_backfilled():
    cfg = config(enabled=("research",), research={"interval": timedelta(minutes=60)})
    daily = record(
        "research",
        slot_key="research_daily",
        slot_at=et("08:30"),
        started=et("08:30"),
        finished=et("08:40"),
    )
    actions = p.plan(et("12:45"), cfg, state([daily]))
    assert actions == [p.Start("research", "scheduled", "research@12:30", et("12:30"))]


def test_replanning_from_the_same_records_starts_nothing_twice():
    sim = Simulator(config()).run(et("07:00"), et("12:00"))
    now = et("12:00")
    first = [a for a in p.plan(now, config(), sim.state(now)) if isinstance(a, p.Start)]
    assert first == []


@settings(max_examples=80, deadline=None)
@given(
    down_from=st.integers(min_value=0, max_value=9 * 60),
    down_for=st.integers(min_value=0, max_value=6 * 60),
)
def test_restart_at_any_time(down_from, down_for):
    """The orchestrator is down for a while; running rows become interrupted; the
    schedule carries on. No slot twice, and the daily runs happen iff still due."""
    base = et("07:00")
    down_start = base + timedelta(minutes=down_from)
    down_end = down_start + timedelta(minutes=down_for)
    sim = Simulator(config(), run_for=timedelta(minutes=20))
    now = base
    restarted = False
    while now <= et("17:00"):
        if down_start <= now < down_end:
            now += timedelta(seconds=30)
            continue
        if not restarted and now >= down_end and down_for:
            restarted = True
            sim.records = [
                dataclasses.replace(r, outcome="interrupted", finished_at=now)
                if r.outcome == "running"
                else r
                for r in sim.records
            ]
        sim.tick(now)
        now += timedelta(seconds=30)
    keys = [(r.agent, r.slot_key) for r in sim.records if r.slot_key is not None]
    assert len(keys) == len(set(keys))
    assert ("research", "research_daily") in keys  # always still due before the cutoff
    cut = p.cutoff(et("07:00").date(), config())
    assert (PM, "morning_session") in keys or down_end >= cut


def test_a_slot_past_its_own_interval_is_not_run_late():
    # Window ending 13:00: the 13:00 slot's interval ends at 14:00, well before the
    # close, so a start at 14:30 would be a backfill.
    from datetime import time as wall

    cfg = config(enabled=(OI,), opportunistic_identifier={"window_end": wall(13, 0)})
    assert p.plan(et("13:59"), cfg, state()) == [p.Start(OI, "scheduled", "oi@13:00", et("13:00"))]
    assert p.plan(et("14:30"), cfg, state()) == []
