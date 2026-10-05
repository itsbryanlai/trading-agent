"""US3: the pause stops the PM, not the analysts (FR-016-FR-018, clarification 5)."""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.orchestrator.support import Simulator, config, et, record, state
from trading_agent.orchestrator import planner as p

PM = "portfolio_manager"


def _research_done():
    return record(
        "research",
        slot_key="research_daily",
        slot_at=et("08:30"),
        started=et("08:30"),
        finished=et("08:40"),
    )


def test_paused_at_the_morning_session_skips_it_and_the_identifier_runs():
    actions = p.plan(et("10:00"), config(), state([_research_done()], paused=True))
    assert (
        p.Skip(PM, "morning_session", "morning_session", et("10:00"), "trading paused") in actions
    )
    assert any(isinstance(a, p.Start) and a.agent == "opportunistic_identifier" for a in actions)
    assert not any(isinstance(a, p.Start) and a.agent == PM for a in actions)


def test_resumed_at_1300_runs_the_pm_once():
    cfg = config()
    skipped = record(
        PM,
        reason="morning_session",
        slot_key="morning_session",
        slot_at=et("10:00"),
        outcome="skipped",
    )
    records = [_research_done(), skipped]
    held = p.plan(et("12:59"), cfg, state(records, latest_report=et("08:35"), paused=True))
    assert p.Hold(PM, "trading paused") in held
    actions = p.plan(et("13:00"), cfg, state(records, latest_report=et("08:35"), paused=False))
    assert p.Start(PM, "event_driven", None, None) in actions


def test_an_unreadable_pause_flag_fails_closed():
    cfg = config()
    actions = p.plan(et("10:00"), cfg, state([_research_done()], paused=None))
    assert (
        p.Skip(PM, "morning_session", "morning_session", et("10:00"), "pause flag unreadable")
        in actions
    )
    morning = record(
        PM,
        reason="morning_session",
        slot_key="morning_session",
        slot_at=et("10:00"),
        started=et("10:00"),
        finished=et("10:05"),
    )
    later = p.plan(
        et("12:00"), cfg, state([_research_done(), morning], latest_report=et("11:00"), paused=None)
    )
    assert not any(isinstance(a, p.Start) and a.agent == PM for a in later)
    assert p.Hold(PM, "pause flag unreadable") in later


def test_research_ignores_the_pause():
    actions = p.plan(et("08:30"), config(), state(paused=True))
    assert actions == [p.Start("research", "scheduled", "research_daily", et("08:30"))]


@settings(max_examples=80, deadline=None)
@given(
    pause_start=st.integers(min_value=0, max_value=8 * 60),
    pause_length=st.integers(min_value=1, max_value=8 * 60),
    reports=st.lists(st.integers(min_value=0, max_value=9 * 60), max_size=8),
)
def test_no_pm_start_while_paused(pause_start, pause_length, reports):
    base = et("08:00")
    start, end = (
        base + timedelta(minutes=pause_start),
        base + timedelta(minutes=pause_start + pause_length),
    )
    sim = Simulator(config(), paused=lambda now: start <= now < end)
    sim.reports = [base + timedelta(minutes=m) for m in reports]
    sim.run(et("07:00"), et("17:00"))
    for run in sim.starts(PM):
        assert not (start <= run.started_at < end)
    # The analysts are unaffected: every Identifier slot still ran.
    assert len(sim.starts("opportunistic_identifier")) == 6


# --- portfolio_manager.run_while_paused (ADR 0021) ---------------------------------


def _pm_starts(actions):
    return [a for a in actions if isinstance(a, p.Start) and a.agent == PM]


def _observing():
    return config(portfolio_manager={"run_while_paused": True})


def test_the_setting_false_still_blocks_the_pm_while_paused():
    cfg = config(portfolio_manager={"run_while_paused": False})
    actions = p.plan(et("10:00"), cfg, state([_research_done()], paused=True))
    assert not _pm_starts(actions)
    assert (
        p.Skip(PM, "morning_session", "morning_session", et("10:00"), "trading paused") in actions
    )


def test_the_setting_true_starts_the_morning_session_while_paused():
    actions = p.plan(et("10:00"), _observing(), state([_research_done()], paused=True))
    assert _pm_starts(actions) == [p.Start(PM, "morning_session", "morning_session", et("10:00"))]


def test_the_setting_true_starts_event_driven_runs_while_paused():
    morning = record(
        PM,
        reason="morning_session",
        slot_key="morning_session",
        slot_at=et("10:00"),
        started=et("10:00"),
        finished=et("10:05"),
    )
    actions = p.plan(
        et("12:00"),
        _observing(),
        state([_research_done(), morning], latest_report=et("11:00"), paused=True),
    )
    assert p.Start(PM, "event_driven", None, None) in actions


def test_the_setting_true_still_blocks_on_an_unknown_flag():
    cfg = _observing()
    actions = p.plan(et("10:00"), cfg, state([_research_done()], paused=None))
    assert not _pm_starts(actions)
    assert (
        p.Skip(PM, "morning_session", "morning_session", et("10:00"), "pause flag unreadable")
        in actions
    )
    morning = record(
        PM,
        reason="morning_session",
        slot_key="morning_session",
        slot_at=et("10:00"),
        started=et("10:00"),
        finished=et("10:05"),
    )
    later = p.plan(
        et("12:00"), cfg, state([_research_done(), morning], latest_report=et("11:00"), paused=None)
    )
    assert not _pm_starts(later)
    assert p.Hold(PM, "pause flag unreadable") in later


def test_the_setting_true_leaves_the_analysts_alone():
    assert p.plan(et("08:30"), _observing(), state(paused=True)) == [
        p.Start("research", "scheduled", "research_daily", et("08:30"))
    ]
