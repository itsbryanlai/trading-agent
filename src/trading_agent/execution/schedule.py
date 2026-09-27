"""What is due at `now` (research E13), from the exchange calendar. No clock:
`now` is always an argument."""

from __future__ import annotations

from datetime import datetime, timedelta

from trading_agent.risk import calendar

MONITOR_INTERVAL = timedelta(minutes=30)
PRE_OPEN_LEAD = timedelta(minutes=60)


def monitor_window(now: datetime) -> tuple[datetime, datetime] | None:
    """The 30-minute stop-loss window containing `now`, clipped to the close
    (early closes included), or None outside market hours."""
    day = calendar.trading_day(now)
    if not calendar.is_session(day):
        return None
    opens, closes = calendar.open_time(day), calendar.close_time(day)
    if not opens <= now < closes:
        return None
    k = (now - opens) // MONITOR_INTERVAL
    start = opens + k * MONITOR_INTERVAL
    return start, min(start + MONITOR_INTERVAL, closes)


def pre_open_due(now: datetime, has_snapshot_before_open_today: bool) -> bool:
    """The pre-open snapshot is due in the hour before a session's open until one
    exists (FR-015)."""
    if has_snapshot_before_open_today:
        return False
    day = calendar.trading_day(now)
    if not calendar.is_session(day):
        return False
    opens = calendar.open_time(day)
    return opens - PRE_OPEN_LEAD <= now < opens
