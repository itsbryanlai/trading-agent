"""NYSE (XNYS) market hours, holidays and early closes, with no credential.

Used by the Risk Gate's service to derive `market_open` and `trading_day` and
to find today's open for the daily-loss baseline. Kept out of the pure core:
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


def open_time(day: date) -> datetime:
    if not _XNYS.is_session(pd.Timestamp(day)):
        raise ValueError(f"{day} is not an NYSE session")
    return _XNYS.session_open(pd.Timestamp(day)).to_pydatetime().astimezone(UTC)
