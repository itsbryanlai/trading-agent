"""View models for the journal: day rows, the comparison chart and usage totals (FR-010).

Pure. Each day row comes from `attribution.parse`: a row whose attribution is another schema
version, or is malformed, still shows its summary and equity and says its attribution can't be
displayed. Every symbol and agent name is returned as plain text for the template to escape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from trading_agent.dashboard import attribution, charts, timefmt
from trading_agent.dashboard.attribution import Attribution, Book
from trading_agent.dashboard.pages import money
from trading_agent.dashboard.pages_activity import Body, body
from trading_agent.dashboard.pages_orders import PAGE

# The equity curve's ranges, except `1d`: a journal has one row a day, so `1d` is shown as `1w`.
RANGES = ("1w", "1m", "all")
DEFAULT_RANGE = "1m"
NEW_YORK = ZoneInfo("America/New_York")
SERIES_CLASSES = 6
ACCOUNT = "account"
NO_DATA = "No journal rows for this range."
UNREADABLE = "Attribution can't be displayed for this day."
USAGE_LABELS = (
    ("written", "Written"),
    ("argued", "Argued"),
    ("no_action", "No action"),
    ("cited", "Cited"),
    ("cited_decisions", "Decisions citing"),
    ("approved", "Approved"),
    ("filled", "Filled"),
)


def parse_query(range_raw: str | None, page_raw: str | None) -> tuple[str, int]:
    name = "1w" if range_raw == "1d" else range_raw
    number = int(page_raw.strip()) if page_raw and PAGE.match(page_raw.strip()) else 1
    return (name if name in RANGES else DEFAULT_RANGE), max(number, 1)


def _never(*_args):
    raise AssertionError("`1d` is mapped to `1w`, so the calendar is never needed")


def since_day(name: str, now: datetime) -> date | None:
    """The first journal day in the range, in New York, or None for all."""
    start = charts.range_start(name, now, is_session=_never, open_time=_never)
    return None if start is None else start.astimezone(NEW_YORK).date()


# --- the chart ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SeriesView:
    name: str
    css: str
    paths: tuple[str, ...]
    dots: tuple[tuple[float, float], ...]
    last: str | None


@dataclass(frozen=True)
class ChartView:
    series: tuple[SeriesView, ...]
    markers: tuple[float, ...]
    min_label: str | None
    max_label: str | None
    first: str | None
    last: str | None
    empty_text: str | None
    height: int


def _at(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=UTC)


def _css(name: str, position: int) -> str:
    return "series-account" if name == ACCOUNT else f"series-{position % SERIES_CLASSES + 1}"


def chart_view(rows: Sequence[Mapping], width: int = 600, height: int = 200) -> ChartView:
    parsed = [(row["trading_day"], attribution.parse(row["per_agent_attribution"])) for row in rows]
    # The account line is the journal's own `equity_close`, so an unreadable attribution row
    # leaves a gap in the analysts' lines but not in the account's. Only a null close is a gap,
    # and after one the line resumes at its true level: it is rebased once, to the range's
    # first non-null close, never restarted.
    series = {
        ACCOUNT: sorted((row["trading_day"], row["equity_close"]) for row in rows),
        **attribution.comparison(parsed),
    }
    rebased = {
        name: charts.rebase([(_at(day), value) for day, value in points])
        for name, points in series.items()
    }
    values = [v for points in rebased.values() for _, v in points if v is not None]
    if not values:
        return ChartView((), (), None, None, None, None, NO_DATA, height)
    shared = (min(values), max(values))
    views = []
    for position, (name, points) in enumerate(rebased.items()):
        chart = charts.polyline(points, width, height, shared)
        last = next((v for _, v in reversed(points) if v is not None), None)
        views.append(
            SeriesView(
                name, _css(name, position - 1), chart.paths, chart.dots,
                None if last is None else f"{last:,.2f}",
            )
        )  # fmt: skip
    days = [day for day, _ in parsed]
    first, last_day = _at(min(days)), _at(max(days))
    missed = {
        day
        for _, row in parsed
        if isinstance(row, Attribution)
        for day in row.missed_sessions
        if min(days) <= day <= max(days)
    }
    markers = tuple(charts.x_position(_at(day), first, last_day, width) for day in sorted(missed))
    return ChartView(
        series=tuple(views),
        markers=markers,
        min_label=f"{shared[0]:,.2f}",
        max_label=f"{shared[1]:,.2f}",
        first=timefmt.day(min(days)),
        last=timefmt.day(max(days)),
        empty_text=None,
        height=height,
    )


# --- usage totals ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageRow:
    agent: str
    counts: tuple[int, ...]


@dataclass(frozen=True)
class UsageView:
    labels: tuple[str, ...]
    rows: tuple[UsageRow, ...]
    note: str | None


def usage_view(rows: Sequence[Mapping]) -> UsageView:
    parsed = [(row["trading_day"], attribution.parse(row["per_agent_attribution"])) for row in rows]
    result = attribution.usage_totals(parsed)
    flagged = len(result.flagged)
    note = None
    if flagged:
        noun = "entry is" if flagged == 1 else "entries are"
        note = f"Totals are partial: {flagged} {noun} left out (unreadable attribution or usage)."
    return UsageView(
        labels=tuple(label for _, label in USAGE_LABELS),
        rows=tuple(
            UsageRow(name, tuple(counts[field] for field, _ in USAGE_LABELS))
            for name, counts in sorted(result.totals.items())
        ),
        note=note,
    )


# --- day rows -------------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldingView:
    symbol: str
    weight: str
    ref_price: str


@dataclass(frozen=True)
class AgentView:
    name: str
    index: str
    day_return: str
    holdings: tuple[HoldingView, ...]
    unpriced: tuple[str, ...]
    skipped_targets: tuple[str, ...]
    exited: tuple[str, ...]
    scaled_by: str | None
    late_reports: int


@dataclass(frozen=True)
class DayView:
    day: str
    equity_open: str
    equity_close: str
    account_return: str | None
    summary: Body
    readable: bool
    unreadable_text: str | None
    missed: tuple[str, ...]
    agents: tuple[AgentView, ...]


def percent(fraction: Decimal) -> str:
    return f"{fraction * 100:+.2f}%"


def _agent(name: str, book: Book) -> AgentView:
    exited = [f"sold: {s}" for s in book.exited_sell]
    exited += [f"holding limit: {s}" for s in book.exited_holding_limit]
    return AgentView(
        name=name,
        index=f"{book.index:,.2f}",
        day_return=percent(book.day_return),
        holdings=tuple(
            HoldingView(h.symbol, f"{h.weight_pct:.2f}%", money(h.ref_price)) for h in book.holdings
        ),
        unpriced=book.unpriced,
        skipped_targets=book.skipped_targets,
        exited=tuple(exited),
        scaled_by=None if book.scaled_by is None else f"{book.scaled_by:f}",
        late_reports=book.late_reports,
    )


def day_view(row: Mapping) -> DayView:
    parsed = attribution.parse(row["per_agent_attribution"])
    readable = isinstance(parsed, Attribution)
    return DayView(
        day=timefmt.day(row["trading_day"]),
        equity_open=money(row["equity_open"]),
        equity_close=money(row["equity_close"]),
        account_return=percent(parsed.account_return)
        if readable and parsed.account_return is not None
        else None,
        summary=body(row["summary_md"]),
        readable=readable,
        unreadable_text=None if readable else UNREADABLE,
        missed=tuple(timefmt.day(d) for d in parsed.missed_sessions) if readable else (),
        agents=tuple(_agent(n, b) for n, b in sorted(parsed.agents.items())) if readable else (),
    )


@dataclass(frozen=True)
class JournalView:
    days: tuple[DayView, ...]
    chart: ChartView
    usage: UsageView
    range_name: str
    ranges: tuple[str, ...]
    page: int
    prev_url: str | None
    next_url: str | None
    empty_text: str | None


def _url(range_name: str, page: int) -> str:
    query = {"range": range_name} if range_name != DEFAULT_RANGE else {}
    if page > 1:
        query["page"] = str(page)
    return "/journal" + (f"?{urlencode(query)}" if query else "")


def journal_view(
    page_rows: Sequence[Mapping],
    range_rows: Sequence[Mapping],
    range_name: str,
    page: int,
    page_size: int,
) -> JournalView:
    """`page_rows` holds up to `page_size + 1`; the extra one only says a next page exists."""
    shown = page_rows[:page_size]
    return JournalView(
        days=tuple(day_view(row) for row in shown),
        chart=chart_view(range_rows),
        usage=usage_view(range_rows),
        range_name=range_name,
        ranges=RANGES,
        page=page,
        prev_url=_url(range_name, page - 1) if page > 1 else None,
        next_url=_url(range_name, page + 1) if len(page_rows) > page_size else None,
        empty_text=None if shown else NO_DATA,
    )
