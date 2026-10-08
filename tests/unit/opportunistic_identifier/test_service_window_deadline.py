"""The trading window, the fetch deadline and rate limiting (specs/011 US3; research O10,
O11, O12). Times come from `risk.calendar`; the clock is a fake, so no test sleeps."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import psycopg
import pytest

from tests.fakes.model import FakeModel
from tests.fakes.oi_market_data import FakeOIMarketData
from tests.fakes.oi_store import FakeOIStore
from tests.unit.opportunistic_identifier.support import Clock, config, messages
from tests.unit.opportunistic_identifier.test_service_happy import (
    TimedMarket,
    answer_for,
    run,
)
from trading_agent.llm.settings import ModelSettings
from trading_agent.opportunistic_identifier.ports import RateLimited
from trading_agent.opportunistic_identifier.service import OIRun
from trading_agent.risk import calendar

ET = ZoneInfo("America/New_York")
THURSDAY = (2026, 10, 8)
SATURDAY = (2026, 10, 10)
HOLIDAY = (2026, 11, 26)  # Thanksgiving
EARLY_CLOSE = (2026, 11, 27)  # close 13:00 ET


def at(day, hh_mm: str) -> datetime:
    hour, minute = (int(part) for part in hh_mm.split(":"))
    return datetime(*day, hour, minute, tzinfo=ET).astimezone(UTC)


def fresh_market(now: datetime) -> FakeOIMarketData:
    data = FakeOIMarketData(quote_time=now - timedelta(minutes=5))
    data.add("AAA", current="190")
    return data


# --- the window ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "when",
    [
        at(SATURDAY, "12:00"),
        at(HOLIDAY, "12:00"),
        at(THURSDAY, "09:45"),
        at(THURSDAY, "09:59"),
        at(THURSDAY, "16:00"),
        at(THURSDAY, "17:30"),
        at(EARLY_CLOSE, "13:01"),
        at(EARLY_CLOSE, "15:00"),
    ],
    ids=lambda w: w.astimezone(ET).strftime("%a %m-%d %H:%M"),
)
def test_outside_the_window_nothing_is_fetched_read_or_written(when, caplog):
    with caplog.at_level(logging.INFO, logger="trading_agent.opportunistic_identifier"):
        outcome, data, model, store, _ = run(
            fresh_market(when), answer_for("AAA"), universe=["AAA"], clock=Clock(when)
        )
    assert outcome.skipped is True and outcome.failure is None and outcome.rows == []
    assert data.calls == [] and model.calls == []
    assert store.reads == [] and store.writes == []
    assert messages(caplog) == [
        "opportunistic_identifier: outside the trading window; nothing to do"
    ]


@pytest.mark.parametrize(
    "when",
    [
        at(THURSDAY, "10:00"),
        at(THURSDAY, "10:01"),
        at(THURSDAY, "15:00"),
        at(THURSDAY, "15:57"),
        at(EARLY_CLOSE, "10:00"),
        at(EARLY_CLOSE, "12:57"),
    ],
    ids=lambda w: w.astimezone(ET).strftime("%a %m-%d %H:%M"),
)
def test_inside_the_window_the_run_goes_ahead(when):
    outcome, data, model, store, _ = run(
        fresh_market(when), answer_for("AAA"), universe=["AAA"], clock=Clock(when)
    )
    assert outcome.skipped is False and [r.symbol for r in store.rows] == ["AAA"]
    assert data.calls and len(model.calls) == 1


@pytest.mark.parametrize("when", [at(THURSDAY, "15:59"), at(EARLY_CLOSE, "12:59")])
def test_the_last_minute_before_the_close_passes_the_window_check_but_has_no_time_to_write(when):
    # The window check lets the run start; one minute of margin before the close then stops
    # the write (a row at the close would expire before it was generated): exit 5.
    outcome, data, model, store, _ = run(
        fresh_market(when), answer_for("AAA"), universe=["AAA"], clock=Clock(when)
    )
    assert outcome.skipped is False and data.calls and len(model.calls) == 1
    assert outcome.failure == "window_closed" and store.writes == []


def test_a_dry_run_ignores_the_window():
    when = at(SATURDAY, "12:00")
    outcome, _, _, store, _ = run(
        fresh_market(when), answer_for("AAA"), universe=["AAA"], clock=Clock(when), dry_run=True
    )
    assert outcome.skipped is False and store.writes == [] and len(outcome.rows) == 1


# --- the close passing mid-run ----------------------------------------------------------


class ClosingModel(FakeModel):
    """Answers, but only after the close has passed on the shared clock."""

    def __init__(self, clock: Clock, when: datetime, answer) -> None:
        super().__init__(answer)
        self.clock, self.when = clock, when

    def complete(self, system, user, schema):
        self.clock.now = self.when
        return super().complete(system, user, schema)


@pytest.mark.parametrize("passed", [timedelta(0), timedelta(seconds=-59), timedelta(minutes=5)])
def test_the_close_passing_before_the_write_writes_nothing_and_logs_an_error(passed, caplog):
    start = at(THURSDAY, "15:55")
    clock = Clock(start)
    close = calendar.close_time(datetime(*THURSDAY).date())
    model = ClosingModel(clock, close + passed, answer_for("AAA"))
    store = FakeOIStore()
    with caplog.at_level(logging.INFO):
        outcome = OIRun(
            fresh_market(start),
            model,
            store,
            config(["AAA"]),
            clock=clock,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        ).run()
    assert outcome.failure == "window_closed" and outcome.rows == [] and store.writes == []
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [
        "opportunistic_identifier: window_closed: the close passed before the write; "
        "nothing written"
    ]


def test_a_write_a_minute_or_more_before_the_close_still_goes_ahead():
    start = at(THURSDAY, "15:55")
    clock = Clock(start)
    close = calendar.close_time(datetime(*THURSDAY).date())
    model = ClosingModel(clock, close - timedelta(minutes=2), answer_for("AAA"))
    store = FakeOIStore()
    outcome = OIRun(
        fresh_market(start),
        model,
        store,
        config(["AAA"]),
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    ).run()
    assert outcome.failure is None and len(store.rows) == 1


# --- the fetch deadline -------------------------------------------------------------------------


def timed_names(clock: Clock, count: int = 10) -> TimedMarket:
    data = TimedMarket()
    data.clock, data.times = clock, []
    for n in range(count):
        data.add(f"N{n:02d}", current=str(190 - n))
    return data


@pytest.mark.parametrize(
    ("provider", "deadline", "calls"),
    [("qwen", 650, 9), ("anthropic", 530, 7)],
)
def test_no_call_starts_after_the_deadline_for_the_provider_and_the_model_still_runs(
    provider, deadline, calls
):
    clock = Clock()
    data = timed_names(clock)
    names = [f"N{n:02d}" for n in range(10)]
    model_settings = ModelSettings(
        provider,
        "qwen3.7-plus" if provider == "qwen" else "claude-sonnet-5-5",
        8000,
        120,
        "medium",
    )
    outcome, _, model, store, _ = run(
        data,
        answer_for("N00"),
        universe=names,
        clock=clock,
        finnhub_calls_per_minute=1,
        model=model_settings,
    )
    assert config(model=model_settings).fetch_deadline(0.0) == deadline
    assert len(data.times) == calls and max(data.times) < deadline
    assert outcome.counts.skipped == {"not_fetched": 8}  # N02 onwards, in both windows
    assert len(model.calls) == 1 and [r.symbol for r in store.rows] == ["N00"]


def test_names_cut_off_by_the_deadline_count_as_not_fetched_and_the_rest_are_used():
    clock = Clock()
    data = timed_names(clock)
    names = [f"N{n:02d}" for n in range(10)]
    outcome, _, model, _, _ = run(
        data, answer_for("N00"), universe=names, clock=clock, finnhub_calls_per_minute=1
    )
    # Qwen: the list, then N00 and N01 whole, then N02's quote and profile: nine calls.
    assert outcome.counts.skipped == {"not_fetched": 8}
    assert outcome.counts.eligible == 2 and outcome.counts.fetched == 3
    assert outcome.failure is None and len(model.calls) == 1


def test_the_deadline_is_measured_from_the_start_of_the_run_not_the_epoch():
    clock = Clock()
    clock.mono = 10_000.0  # a process that has been up a while
    data = timed_names(clock, 3)
    outcome, _, _, _, _ = run(data, answer_for("N00"), universe=["N00", "N01", "N02"], clock=clock)
    assert outcome.counts.skipped == {} and outcome.counts.fetched == 3


# --- rate limiting ------------------------------------------------------------------------------


def test_a_429_backs_off_for_a_minute_and_the_run_continues():
    clock = Clock()
    data = timed_names(clock, 3)
    data.fail("quote", "N00", error=RateLimited())
    outcome, _, _, store, _ = run(
        data,
        answer_for("N01"),
        universe=["N00", "N01", "N02"],
        clock=clock,
        finnhub_calls_per_minute=20,
    )
    quote_times = [
        t for (call, symbol), t in zip(data.calls, data.times, strict=True) if call == "quote"
    ]
    assert quote_times[1] - quote_times[0] >= 60  # N01's quote waits out the back-off
    assert outcome.counts.skipped == {"rate_limited": 1} and outcome.failure is None
    assert [r.symbol for r in store.rows] == ["N01"]


def test_the_backoff_is_one_time_and_the_pace_returns_afterwards():
    clock = Clock()
    data = timed_names(clock, 3)
    data.fail("quote", "N00", error=RateLimited())
    run(data, universe=["N00", "N01", "N02"], clock=clock, finnhub_calls_per_minute=20)
    gaps = [b - a for a, b in zip(data.times[2:], data.times[3:], strict=False)]
    assert gaps and all(g == 3.0 for g in gaps)


def test_rate_limiting_until_the_deadline_fails_the_run_after_the_last_call_that_fits():
    clock = Clock()
    data = timed_names(clock, 20)
    data.fail("quote", error=RateLimited())
    names = [f"N{n:02d}" for n in range(20)]
    outcome, _, model, store, _ = run(
        data, universe=names, clock=clock, finnhub_calls_per_minute=60
    )
    # The list at 0 s, the first quote at 3 s, then one a minute: 3, 63, ... 603 s is 11 calls.
    quotes = [t for (call, _), t in zip(data.calls, data.times, strict=True) if call == "quote"]
    assert quotes == [3.0 + 60 * n for n in range(11)]
    assert max(data.times) < 650
    assert outcome.counts.skipped == {"rate_limited": 11, "not_fetched": 9}
    assert outcome.failure == "market_data_unavailable" and model.calls == []
    assert len(store.rows) == 1 and store.rows[0].direction == "no_action"


# --- the database refusing a row written at the close (review L5) ------------------------------


class Violation(psycopg.errors.CheckViolation):
    """A check violation carrying a constraint name, as the server sends one."""

    def __init__(self, constraint: str) -> None:
        super().__init__("violates a check constraint")
        self._constraint = constraint

    @property
    def diag(self):
        return SimpleNamespace(constraint_name=self._constraint)


def _run_with_violation(constraint: str):
    when = at(THURSDAY, "11:00")
    store = FakeOIStore(fail_write=Violation(constraint))
    return run(
        fresh_market(when), answer_for("AAA"), universe=["AAA"], clock=Clock(when), store=store
    )


def test_a_row_the_database_refuses_for_expiring_before_it_was_generated_is_window_closed(
    caplog,
):
    # The close can pass between the clock check and the insert: the constraint says so.
    with caplog.at_level(logging.INFO):
        outcome, _, _, store, _ = _run_with_violation("reports_expires_after_generated")
    assert outcome.failure == "window_closed" and outcome.rows == [] and store.writes == []
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [
        "opportunistic_identifier: window_closed: the close passed before the write; "
        "nothing written"
    ]
    assert not [m for m in messages(caplog) if m.startswith("opportunistic_identifier: wrote")]


@pytest.mark.parametrize(
    "constraint",
    ["reports_sources_required_when_actionable", "reports_buy_size_positive", "", None],
)
def test_any_other_check_violation_still_reaches_the_caller_as_a_database_error(constraint):
    with pytest.raises(psycopg.errors.CheckViolation):
        _run_with_violation(constraint)
