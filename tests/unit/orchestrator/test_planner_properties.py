"""SC-003 and SC-006 as properties: whatever the reports and outcomes, ADR 0011's
spacing, wait and cutoff hold, and nothing daily runs twice."""

from __future__ import annotations

from datetime import timedelta

from hypothesis import event, given, settings
from hypothesis import strategies as st

from tests.unit.orchestrator.support import EARLY_CLOSE, MONDAY, Simulator, config, et
from trading_agent.orchestrator import planner as p

PM = "portfolio_manager"
SPACING = timedelta(minutes=30)
WAIT = timedelta(minutes=5)


@settings(max_examples=150, deadline=None)
@given(
    day=st.sampled_from([MONDAY, EARLY_CLOSE]),
    reports=st.lists(st.integers(min_value=0, max_value=9 * 60), max_size=12),
    failures=st.lists(st.booleans(), max_size=20),
    run_minutes=st.integers(min_value=1, max_value=9),
)
def test_adr_0011_holds_for_any_report_pattern(day, reports, failures, run_minutes):
    base = et("07:00", day)
    outcomes = iter(failures)

    def outcome(action, now):
        return "failed" if next(outcomes, False) else "succeeded"

    sim = Simulator(config(), run_for=timedelta(minutes=run_minutes), outcome=outcome)
    sim.reports = sorted(base + timedelta(minutes=m) for m in reports)
    sim.run(et("07:00", day), et("17:00", day))

    cut = p.cutoff(day, config())
    pm = sorted(r.started_at for r in sim.starts(PM))
    event(f"pm runs: {len(pm)}")
    for earlier, later in zip(pm, pm[1:], strict=False):
        assert later - earlier >= SPACING
    for run in sim.starts(PM):
        assert run.started_at < cut
        if run.reason == "event_driven":
            newest = max(t for t in sim.reports if t <= run.started_at)
            assert run.started_at >= newest + WAIT
    daily = [(r.agent, r.slot_key) for r in sim.records if r.slot_key is not None]
    assert len(daily) == len(set(daily))  # no slot claimed twice


def test_the_property_reaches_event_driven_runs():
    sim = Simulator(config())
    sim.reports = [et("11:02"), et("13:10")]
    sim.run(et("07:00"), et("17:00"))
    assert [r.reason for r in sim.starts(PM)] == [
        "morning_session",
        "event_driven",
        "event_driven",
    ]
