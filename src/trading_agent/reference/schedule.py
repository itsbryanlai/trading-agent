"""When the job fetches (research D7). Pure: `now` is an argument.

Every time judgement comes from trading_agent.risk.calendar, the module the gate
and Execution use, so the three can't disagree (ADR 0013 §4).
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from trading_agent.risk import calendar

FETCH_START_ET = time(8, 0)
_NEW_YORK = ZoneInfo("America/New_York")


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("timezone-aware datetime required")


def fetch_allowed(now: datetime) -> bool:
    """From 08:00 ET until the close on an XNYS session day (FR-013, FR-015).

    The main run is just the first tick after 08:00; a late start catches up on
    its first tick.
    """
    _require_aware(now)
    day = calendar.trading_day(now)
    if not calendar.is_session(day):
        return False
    start = datetime.combine(day, FETCH_START_ET, tzinfo=_NEW_YORK)
    return start <= now < calendar.close_time(day)


def open_warning_due(now: datetime, warned_on: date | None) -> bool:
    """The first tick at or after the open, once per trading day (FR-025)."""
    _require_aware(now)
    day = calendar.trading_day(now)
    if warned_on == day or not calendar.is_session(day):
        return False
    return calendar.open_time(day) <= now < calendar.close_time(day)
