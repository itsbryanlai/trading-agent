"""US1: scheduled runs start at their slots, once (FR-006, FR-007, FR-009-FR-012)."""

from __future__ import annotations

from tests.unit.orchestrator.support import SATURDAY, Simulator, config, et, record, state
from trading_agent.orchestrator import planner as p


def test_research_at_0830_once():
    cfg = config()
    now = et("08:30", seconds=10)
    actions = p.plan(now, cfg, state())
    assert actions == [p.Start("research", "scheduled", "research_daily", et("08:30"))]
    done = record("research", slot_key="research_daily", slot_at=et("08:30"), started=now)
    assert p.plan(et("08:31"), cfg, state([done])) == []


def test_morning_session_and_identifier_at_1000():
    cfg = config()
    research = record(
        "research",
        slot_key="research_daily",
        slot_at=et("08:30"),
        started=et("08:30"),
        finished=et("08:40"),
    )
    actions = p.plan(et("10:00"), cfg, state([research]))
    assert actions == [
        p.Start("portfolio_manager", "morning_session", "morning_session", et("10:00")),
        p.Start("opportunistic_identifier", "scheduled", "oi@10:00", et("10:00")),
    ]


def test_disabled_agents_never_start():
    cfg = config(enabled=("opportunistic_identifier",))
    sim = Simulator(cfg).run(et("07:00"), et("17:00"))
    assert {r.agent for r in sim.records} == {"opportunistic_identifier"}


def test_weekend_starts_nothing():
    sim = Simulator(config()).run(et("07:00", SATURDAY), et("17:00", SATURDAY))
    assert sim.records == []


def test_overlap_skips_the_slot_once():
    cfg = config()
    hung = record(
        "opportunistic_identifier", slot_key="oi@10:00", slot_at=et("10:00"), started=et("10:00")
    )
    actions = p.plan(et("11:00"), cfg, state([hung]))
    skips = [a for a in actions if isinstance(a, p.Skip)]
    assert skips == [
        p.Skip(
            "opportunistic_identifier",
            "scheduled",
            "oi@11:00",
            et("11:00"),
            "previous run in progress",
        )
    ]


def test_a_full_day_starts_exactly_the_slots():
    sim = Simulator(config()).run(et("07:00"), et("17:00"))
    starts = sorted((r.started_at, r.agent, r.reason) for r in sim.starts())
    assert starts == sorted(
        [
            (et("08:30"), "research", "scheduled"),
            (et("10:00"), "portfolio_manager", "morning_session"),
            *[(et(f"{h}:00"), "opportunistic_identifier", "scheduled") for h in range(10, 16)],
        ]
    )


def test_every_start_carries_its_slot_key():
    sim = Simulator(config()).run(et("07:00"), et("12:00"))
    keys = {(r.agent, r.slot_key) for r in sim.starts()}
    assert ("research", "research_daily") in keys
    assert ("portfolio_manager", "morning_session") in keys
    assert ("opportunistic_identifier", "oi@11:00") in keys


def test_on_time_versus_catch_up_for_research():
    cfg = config()
    assert p.plan(et("08:30", seconds=20), cfg, state())[0].reason == "scheduled"
    assert p.plan(et("08:31"), cfg, state())[0].reason == "catch_up"
    assert p.plan(et("08:31"), cfg, state())[0].slot_key == "research_daily"


def test_morning_held_back_by_research_is_still_the_morning_session():
    cfg = config()
    research = record(
        "research",
        reason="catch_up",
        slot_key="research_daily",
        slot_at=et("08:30"),
        started=et("09:50"),
    )
    assert [
        a for a in p.plan(et("10:00"), cfg, state([research])) if a.agent == "portfolio_manager"
    ] == []
    finished = record(
        "research",
        reason="catch_up",
        slot_key="research_daily",
        slot_at=et("08:30"),
        started=et("09:50"),
        finished=et("10:07"),
    )
    pm = [a for a in p.plan(et("10:07"), cfg, state([finished])) if a.agent == "portfolio_manager"]
    assert pm == [p.Start("portfolio_manager", "morning_session", "morning_session", et("10:00"))]


def test_research_first_then_no_pm_in_the_same_tick():
    cfg = config()
    actions = p.plan(et("10:00"), cfg, state())  # Research never ran today
    assert [a.agent for a in actions] == ["research", "opportunistic_identifier"]
    assert actions[0].reason == "catch_up"
