"""Chart geometry on the server: SVG polylines and the ranges they cover (research D20).

Pure: points and times are arguments. The caller supplies the exchange calendar as two
functions, so the `1d` range follows the NYSE without this module reading anything.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

_NEW_YORK = ZoneInfo("America/New_York")
_PAD = 4
_WEEK = 7
_MONTH = 30
_MAX_SESSION_LOOKBACK = 14  # days: longer than any run of closures

Point = tuple[datetime, Decimal | None]


@dataclass(frozen=True)
class Chart:
    """What the template draws: `<polyline points=…>` per path, a dot per lone value."""

    paths: tuple[str, ...] = ()
    dots: tuple[tuple[float, float], ...] = ()
    min_label: str | None = None
    max_label: str | None = None
    first_at: datetime | None = None
    last_at: datetime | None = None

    @property
    def empty(self) -> bool:
        return not self.paths and not self.dots


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("a timezone-aware datetime is required")


def _label(value: Decimal) -> str:
    return f"{value:,.2f}"


def x_position(at: datetime, first_at: datetime, last_at: datetime, width: int = 600) -> float:
    """Where a time falls on the x axis of a chart spanning `first_at` to `last_at`."""
    span = (last_at - first_at).total_seconds()
    if span == 0:
        return round(width / 2, 1)
    return round(_PAD + (at - first_at).total_seconds() / span * (width - 2 * _PAD), 1)


def polyline(
    points: Sequence[Point],
    width: int = 600,
    height: int = 200,
    value_range: tuple[Decimal, Decimal] | None = None,
) -> Chart:
    """Scale `(time, value)` points into a `width` x `height` box.

    A missing value breaks the line, so a gap is never drawn as a value. A run of one point
    is a dot. No values at all is the empty chart. Charts that share an axis (several lines
    compared on one plot) pass the same `value_range`; their points should span the same
    times, which the x axis is taken from, gaps included.
    """
    for at, _ in points:
        _require_aware(at)
    ordered = sorted(points, key=lambda point: point[0])
    values = [value for _, value in ordered if value is not None]
    if not values:
        return Chart()
    low, high = value_range if value_range is not None else (min(values), max(values))
    first_at, last_at = ordered[0][0], ordered[-1][0]

    def place(at: datetime, value: Decimal) -> tuple[float, float]:
        x = x_position(at, first_at, last_at, width)
        y = (
            height / 2
            if high == low
            else _PAD + float((high - value) / (high - low)) * (height - 2 * _PAD)
        )
        return x, round(y, 1)

    runs: list[list[tuple[float, float]]] = [[]]
    for at, value in ordered:
        if value is None:
            if runs[-1]:
                runs.append([])
        else:
            runs[-1].append(place(at, value))
    paths = tuple(" ".join(f"{x},{y}" for x, y in run) for run in runs if len(run) > 1)
    dots = tuple(run[0] for run in runs if len(run) == 1)
    return Chart(paths, dots, _label(low), _label(high), first_at, last_at)


def rebase(series: Sequence[Point]) -> list[Point]:
    """The series scaled so its first value is 100; gaps stay gaps.

    A series whose first value is zero can't be scaled, so it becomes all gaps.
    """
    base = next((value for _, value in series if value is not None), None)
    if base is None or base == 0:
        return [(at, None) for at, _ in series]
    return [(at, None if value is None else value * 100 / base) for at, value in series]


def range_start(
    name: str,
    now: datetime,
    *,
    is_session: Callable[[date], bool],
    open_time: Callable[[date], datetime],
) -> datetime | None:
    """Where a range begins, or None for `all`.

    `1d` is the open of the current NYSE session, or of the last one when none has opened yet
    today (a weekend, a holiday, before the open). `1w` and `1m` (7 and 30 days) begin at the
    start of that New York day.
    """
    _require_aware(now)
    if name == "all":
        return None
    if name == "1d":
        return _last_open(now, is_session, open_time)
    days = {"1w": _WEEK, "1m": _MONTH}.get(name)
    if days is None:
        raise ValueError(f"unknown range {name!r}")
    day = now.astimezone(_NEW_YORK).date() - timedelta(days=days)
    return datetime(day.year, day.month, day.day, tzinfo=_NEW_YORK)


def _last_open(
    now: datetime, is_session: Callable[[date], bool], open_time: Callable[[date], datetime]
) -> datetime:
    day = now.astimezone(_NEW_YORK).date()
    for _ in range(_MAX_SESSION_LOOKBACK):
        if is_session(day) and open_time(day) <= now:
            return open_time(day)
        day -= timedelta(days=1)
    raise ValueError("no NYSE session found before now")
