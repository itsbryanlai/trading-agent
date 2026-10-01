"""What is due now (research O8-O11). Pure: no clock, database, processes or
environment; `now` and everything else is an argument.

The service builds a `State` from the run records, asks `plan` what to do, and
applies the actions. Every rule lives here so it can be tested exhaustively:
slots and the cutoff, on-time versus catch-up, the morning session and its
Research-first wait, ADR 0011's event-driven PM runs, the pause, overlap skips
and timeouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from trading_agent.orchestrator.config import (
    IDENTIFIER,
    PORTFOLIO_MANAGER,
    RESEARCH,
    ScheduleConfig,
)
from trading_agent.risk import calendar

NEW_YORK = ZoneInfo("America/New_York")
# A daily slot started within one tick of its time is on time; later is a
# catch-up (research O9).
ON_TIME = timedelta(seconds=30)

SCHEDULED = "scheduled"
MORNING_SESSION = "morning_session"
EVENT_DRIVEN = "event_driven"
CATCH_UP = "catch_up"

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
TIMED_OUT = "timed_out"
INTERRUPTED = "interrupted"
SKIPPED = "skipped"

RESEARCH_DAILY = "research_daily"
MORNING_KEY = "morning_session"

PAUSED = "trading paused"
PAUSE_UNREADABLE = "pause flag unreadable"
IN_PROGRESS = "previous run in progress"


@dataclass(frozen=True)
class RunRecord:
    id: object
    agent: str
    trading_day: date
    reason: str
    slot_key: str | None
    slot_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    outcome: str
    pgid: int | None = None


@dataclass(frozen=True)
class State:
    today: tuple[RunRecord, ...]
    running: tuple[RunRecord, ...]
    last_pm_start: datetime | None
    last_successful_pm_start: datetime | None
    latest_report: datetime | None
    paused: bool | None  # None: the flag couldn't be read


@dataclass(frozen=True)
class Start:
    agent: str
    reason: str
    slot_key: str | None
    slot_at: datetime | None


@dataclass(frozen=True)
class Skip:
    agent: str
    reason: str
    slot_key: str
    slot_at: datetime
    why: str


@dataclass(frozen=True)
class Stop:
    run_id: object
    agent: str


@dataclass(frozen=True)
class Hold:
    """An event-driven PM run is due but paused: logged, never recorded (O11)."""

    agent: str
    why: str


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("timezone-aware datetime required")


def at(day: date, wall: time) -> datetime:
    """An ET wall-clock time on `day`, as an aware datetime (DST handled by zoneinfo)."""
    return datetime.combine(day, wall, tzinfo=NEW_YORK)


def _key(prefix: str, when: datetime) -> str:
    return f"{prefix}@{when.astimezone(NEW_YORK):%H:%M}"


def cutoff(day: date, cfg: ScheduleConfig) -> datetime | None:
    """No PM start at or after this: 15:30 ET, or 30 minutes before an early close."""
    if not calendar.is_session(day):
        return None
    pm = cfg.portfolio_manager
    return min(at(day, pm.last_start), calendar.close_time(day) - pm.before_close)


def research_slots(day: date, cfg: ScheduleConfig) -> list[tuple[str, datetime]]:
    if not calendar.is_session(day):
        return []
    daily = at(day, cfg.research.daily_at)
    slots = [(RESEARCH_DAILY, daily)]
    if cfg.research.interval is not None:
        end = cutoff(day, cfg)
        when = daily + cfg.research.interval
        while when < end:
            slots.append((_key("research", when), when))
            when += cfg.research.interval
    return slots


def oi_slots(day: date, cfg: ScheduleConfig) -> list[tuple[str, datetime]]:
    if not calendar.is_session(day):
        return []
    oi = cfg.identifier
    last = min(
        at(day, oi.window_end),
        calendar.close_time(day) - cfg.portfolio_manager.before_close,
    )
    slots = []
    when = at(day, oi.window_start)
    while when <= last:
        slots.append((_key("oi", when), when))
        when += oi.interval
    return slots


def _current(
    slots: list[tuple[str, datetime]], now: datetime, interval: timedelta, close: datetime
) -> tuple[str, datetime] | None:
    """The latest slot at or before `now`, if still inside its own interval and
    before the close. Earlier slots are never backfilled."""
    due = [s for s in slots if s[1] <= now]
    if not due:
        return None
    key, when = due[-1]
    if now >= when + interval or now >= close:
        return None
    return key, when


def plan(now: datetime, cfg: ScheduleConfig, state: State) -> list:
    _require_aware(now)
    actions: list = []

    # Timeouts apply every day, trading or not: nothing outlives its limit.
    for run in state.running:
        agent = cfg.agent(run.agent)
        if run.started_at is not None and now - run.started_at >= agent.timeout:
            actions.append(Stop(run.id, run.agent))

    day = calendar.trading_day(now)
    if not calendar.is_session(day):
        return actions
    end = cutoff(day, cfg)
    close = calendar.close_time(day)
    claimed = {(r.agent, r.slot_key) for r in state.today if r.slot_key is not None}
    # A run being stopped this tick no longer counts as running: the service stops
    # it before applying anything else, so its next slot isn't skipped for a run
    # that is already over (adversarial review L1).
    stopping = {a.run_id for a in actions}
    running = {r.agent for r in state.running if r.id not in stopping}
    starting: set[str] = set()

    # --- Research -------------------------------------------------------------------
    research = cfg.research
    research_due_now = False
    if research.enabled:
        slots = research_slots(day, cfg)
        daily_key, daily_at = slots[0]
        if (RESEARCH, daily_key) not in claimed and daily_at <= now < end:
            research_due_now = True
            if RESEARCH in running:
                actions.append(Skip(RESEARCH, SCHEDULED, daily_key, daily_at, IN_PROGRESS))
            else:
                reason = SCHEDULED if now - daily_at <= ON_TIME else CATCH_UP
                actions.append(Start(RESEARCH, reason, daily_key, daily_at))
                starting.add(RESEARCH)
        elif research.interval is not None:
            current = _current(slots[1:], now, research.interval, close)
            if current is not None and (RESEARCH, current[0]) not in claimed:
                key, when = current
                if RESEARCH in running:
                    actions.append(Skip(RESEARCH, SCHEDULED, key, when, IN_PROGRESS))
                else:
                    actions.append(Start(RESEARCH, SCHEDULED, key, when))
                    starting.add(RESEARCH)

    # --- Portfolio Manager ----------------------------------------------------------
    pm = cfg.portfolio_manager
    if pm.enabled:
        actions.extend(
            _portfolio_manager(
                now, cfg, state, day, end, claimed, running, research_due_now, starting
            )
        )

    # --- Opportunistic Identifier -----------------------------------------------------
    oi = cfg.identifier
    if oi.enabled:
        current = _current(oi_slots(day, cfg), now, oi.interval, close)
        if current is not None and (IDENTIFIER, current[0]) not in claimed:
            key, when = current
            if IDENTIFIER in running:
                actions.append(Skip(IDENTIFIER, SCHEDULED, key, when, IN_PROGRESS))
            else:
                actions.append(Start(IDENTIFIER, SCHEDULED, key, when))
    return actions


def _pause_block(paused: bool | None) -> str | None:
    if paused is None:
        return PAUSE_UNREADABLE
    return PAUSED if paused else None


def _portfolio_manager(now, cfg, state, day, end, claimed, running, research_due_now, starting):
    pm = cfg.portfolio_manager
    morning_at = at(day, pm.morning_session)
    research_busy = RESEARCH in running or RESEARCH in starting or research_due_now

    # The morning session: once a day, after Research's report (O10).
    if (PORTFOLIO_MANAGER, MORNING_KEY) not in claimed:
        if not (morning_at <= now < end):
            return []
        if research_busy and cfg.research.enabled:
            return []  # wait for Research; still the morning session when it starts
        blocked = _pause_block(state.paused)
        reason = _morning_reason(now, morning_at, state)
        if blocked is not None:
            return [Skip(PORTFOLIO_MANAGER, reason, MORNING_KEY, morning_at, blocked)]
        if PORTFOLIO_MANAGER in running:
            return [Skip(PORTFOLIO_MANAGER, reason, MORNING_KEY, morning_at, IN_PROGRESS)]
        return [Start(PORTFOLIO_MANAGER, reason, MORNING_KEY, morning_at)]

    # Event-driven runs (ADR 0011, FR-013): only once the morning slot is done.
    if PORTFOLIO_MANAGER in running or now >= end:
        return []
    report = state.latest_report
    if report is None:
        return []
    considered = state.last_successful_pm_start
    if considered is not None and report <= considered:
        return []
    if now < report + pm.report_wait:
        return []
    if state.last_pm_start is not None and now < state.last_pm_start + pm.min_spacing:
        return []
    blocked = _pause_block(state.paused)
    if blocked is not None:
        return [Hold(PORTFOLIO_MANAGER, blocked)]
    return [Start(PORTFOLIO_MANAGER, EVENT_DRIVEN, None, None)]


def _morning_reason(now: datetime, morning_at: datetime, state: State) -> str:
    """On time, or held back only by a Research run that was going at the slot's
    time: the morning session. Otherwise the orchestrator missed it: a catch-up."""
    if now - morning_at <= ON_TIME:
        return MORNING_SESSION
    for run in state.today:
        if run.agent != RESEARCH or run.started_at is None:
            continue
        if run.started_at <= morning_at and (
            run.finished_at is None or run.finished_at >= morning_at - ON_TIME
        ):
            return MORNING_SESSION
    return CATCH_UP
