"""US4: timeouts and no early retries (FR-005, FR-019)."""

from __future__ import annotations

from datetime import date, timedelta

from tests.unit.orchestrator.support import SATURDAY, config, et, record, state
from trading_agent.orchestrator import planner as p

OI = "opportunistic_identifier"


def test_a_run_past_its_timeout_is_stopped():
    hung = record(OI, slot_key="oi@11:00", slot_at=et("11:00"), started=et("11:00"))
    cfg = config(enabled=(OI,))
    assert p.Stop(hung.id, OI) in p.plan(et("11:10"), cfg, state([hung]))
    assert not [
        a
        for a in p.plan(et("11:10") - timedelta(seconds=1), cfg, state([hung]))
        if isinstance(a, p.Stop)
    ]


def test_timeouts_apply_on_non_trading_days_too():
    friday = date(2026, 9, 25)
    hung = record(
        OI,
        slot_key="oi@15:00",
        slot_at=et("15:00", friday),
        started=et("15:00", friday),
        day=friday,
    )
    actions = p.plan(et("10:00", SATURDAY), config(), state([hung], day=SATURDAY))
    assert actions == [p.Stop(hung.id, OI)]


def test_a_failed_identifier_run_waits_for_its_next_slot():
    failed = record(
        OI,
        slot_key="oi@11:00",
        slot_at=et("11:00"),
        started=et("11:00"),
        finished=et("11:01"),
        outcome="failed",
    )
    cfg = config(enabled=(OI,))
    assert p.plan(et("11:30"), cfg, state([failed])) == []
    assert p.plan(et("12:00"), cfg, state([failed])) == [
        p.Start(OI, "scheduled", "oi@12:00", et("12:00"))
    ]


def test_a_run_being_stopped_does_not_cost_its_next_slot():
    # Timeout equal to the hour: the 10:00 run is stopped at 11:00, and the 11:00
    # slot starts in the same tick rather than being skipped for a run that is over.
    cfg = config(enabled=(OI,), opportunistic_identifier={"timeout": timedelta(minutes=60)})
    hung = record(OI, slot_key="oi@10:00", slot_at=et("10:00"), started=et("10:00"))
    actions = p.plan(et("11:00"), cfg, state([hung]))
    assert actions == [
        p.Stop(hung.id, OI),
        p.Start(OI, "scheduled", "oi@11:00", et("11:00")),
    ]
