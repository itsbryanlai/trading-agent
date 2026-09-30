"""US2: event-driven PM runs (ADR 0011, FR-013, FR-014, clarifications 2 and 3)."""

from __future__ import annotations

from tests.unit.orchestrator.support import EARLY_CLOSE, config, et, record, state
from trading_agent.orchestrator import planner as p

PM = "portfolio_manager"


def _pm_only():
    return config(enabled=("portfolio_manager",))


def _morning(started="10:00", day=None, outcome="succeeded", finished=None):
    kwargs = {} if day is None else {"day": day}
    start = et(started, **kwargs)
    return record(
        PM,
        reason="morning_session",
        slot_key="morning_session",
        slot_at=start,
        started=start,
        finished=finished or start,
        outcome=outcome,
        **kwargs,
    )


def _event(started, outcome="succeeded", day=None):
    kwargs = {} if day is None else {"day": day}
    start = et(started, **kwargs)
    finished = None if outcome == "running" else start
    return record(
        PM, reason="event_driven", started=start, finished=finished, outcome=outcome, **kwargs
    )


def _plan(now, records, report, **kwargs):
    return [
        a
        for a in p.plan(now, _pm_only(), state(records, latest_report=report, **kwargs))
        if a.agent == PM
    ]


START = p.Start(PM, "event_driven", None, None)


def test_a_report_after_the_morning_session_triggers_one_run_after_the_wait():
    records = [_morning()]
    assert _plan(et("11:06"), records, et("11:02")) == []
    assert _plan(et("11:07"), records, et("11:02")) == [START]


def test_a_burst_is_handled_in_one_run_after_spacing():
    records = [_morning(), _event("11:07")]
    assert _plan(et("11:36"), records, et("11:25")) == []  # 29 min after the last run
    assert _plan(et("11:37"), records, et("11:25")) == [START]


def test_the_wait_is_measured_from_the_newest_report():
    records = [_morning(), _event("11:00")]
    assert _plan(et("11:33"), records, et("11:29")) == []
    assert _plan(et("11:34"), records, et("11:29")) == [START]


def test_no_run_at_or_after_the_cutoff():
    records = [_morning()]
    assert _plan(et("15:33"), records, et("15:28")) == []
    records = [_morning(), _event("15:00")]
    assert _plan(et("15:30"), records, et("15:24")) == []
    assert _plan(et("15:29"), records, et("15:24")) == []  # spacing


def test_early_close_cutoff():
    day = EARLY_CLOSE
    records = [_morning(day=day)]
    assert _plan(et("12:25", day), records, et("12:20", day), day=day) == [START]
    assert _plan(et("12:31", day), records, et("12:26", day), day=day) == []


def test_nothing_new_means_no_run():
    records = [_morning(started="10:00")]
    assert _plan(et("12:00"), records, et("09:45")) == []
    assert _plan(et("12:00"), records, None) == []


def test_reports_before_the_morning_session_trigger_no_separate_run():
    # Before the morning session: no event-driven run, whatever the reports.
    assert _plan(et("09:59"), [], et("09:45")) == []


def test_a_failed_run_is_retried_after_spacing_even_with_no_newer_report():
    for outcome in ("failed", "timed_out", "interrupted"):
        records = [_morning(), _event("11:07", outcome=outcome)]
        assert _plan(et("11:36"), records, et("11:02")) == [], outcome
        assert _plan(et("11:37"), records, et("11:02")) == [START], outcome


def test_newer_than_the_last_success_not_the_last_attempt():
    records = [_morning(), _event("11:00", outcome="failed")]
    # The 10:40 report is older than the failed 11:00 run but newer than the 10:00 success.
    assert _plan(et("11:30"), records, et("10:40")) == [START]


def test_no_new_start_while_a_pm_run_is_in_progress():
    records = [_morning(), _event("11:00", outcome="running")]
    actions = _plan(et("11:45"), records, et("11:20"))
    assert not any(isinstance(a, p.Start) for a in actions)


def test_a_failed_morning_session_is_retried_through_the_event_rule():
    records = [_morning(outcome="failed")]
    # Research's 08:31 report was never considered.
    assert _plan(et("10:29"), records, et("08:31")) == []
    assert _plan(et("10:30"), records, et("08:31")) == [START]
