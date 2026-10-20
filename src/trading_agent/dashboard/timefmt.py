"""Times as the owner reads them (research D19).

Pure: every function takes its times as arguments and never reads the clock. A moment is
shown in the owner's zone and in New York, each converted with `zoneinfo`, so daylight-saving
changes are right on both sides: `19 Oct 09:12 SGT (18 Oct 21:12 ET)`.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

# Current tzdata gives Singapore the numeric name "+08"; the owner knows it as SGT.
_OWNER_ABBREVIATIONS = {"Asia/Singapore": "SGT"}


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("a timezone-aware datetime is required")


def _label(local: datetime) -> str:
    return f"{local.day} {local:%b %H:%M}"


def moment(ts: datetime, zone: ZoneInfo) -> str:
    """`19 Oct 09:12 SGT (18 Oct 21:12 ET)`; EDT and EST are both shown as ET."""
    _require_aware(ts)
    owner = ts.astimezone(zone)
    name = _OWNER_ABBREVIATIONS.get(zone.key or "") or owner.tzname() or ""
    new_york = ts.astimezone(NEW_YORK)
    return f"{_label(owner)} {name} ({_label(new_york)} ET)"


def age(now: datetime, ts: datetime) -> str:
    """`just now`, `3 min old`, `5 h old` or `2 d old`."""
    _require_aware(now)
    _require_aware(ts)
    seconds = (now - ts).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min old"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h old"
    return f"{int(seconds // 86400)} d old"


def day(value: date) -> str:
    """`Mon 19 Oct`, for dates that carry no time (trading days)."""
    return f"{value:%a} {value.day} {value:%b}"
