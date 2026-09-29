"""NYSE (XNYS) market hours, holidays and early closes, with no credential.

Used by the Risk Gate's service to derive `market_open` and `trading_day` and
to find today's open for the daily-loss baseline. Execution uses the same
module for every time judgement, so the two can't disagree (ADR 0013). Kept out of the pure core:
the core receives these as plain values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import exchange_calendars
import pandas as pd

_XNYS = exchange_calendars.get_calendar("XNYS")
_NEW_YORK = ZoneInfo("America/New_York")


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("timezone-aware datetime required")


def market_open(now: datetime) -> bool:
    _require_aware(now)
    minute = pd.Timestamp(now).tz_convert("UTC").floor("min")
    return bool(_XNYS.is_open_on_minute(minute))


def trading_day(now: datetime) -> date:
    _require_aware(now)
    return now.astimezone(_NEW_YORK).date()


def is_session(day: date) -> bool:
    return bool(_XNYS.is_session(pd.Timestamp(day)))


def open_time(day: date) -> datetime:
    if not is_session(day):
        raise ValueError(f"{day} is not an NYSE session")
    return _XNYS.session_open(pd.Timestamp(day)).to_pydatetime().astimezone(UTC)


def close_time(day: date) -> datetime:
    """The session's close in UTC, early closes included."""
    if not is_session(day):
        raise ValueError(f"{day} is not an NYSE session")
    return _XNYS.session_close(pd.Timestamp(day)).to_pydatetime().astimezone(UTC)


def previous_session(day: date) -> date:
    """The last NYSE session strictly before `day`, whether or not `day` is one.

    Used by the reference-data job's symbol window (specs/004-reference-data D10).
    """
    stamp = pd.Timestamp(day)
    if is_session(day):
        return _XNYS.previous_session(stamp).date()
    return _XNYS.date_to_session(stamp, direction="previous").date()
