"""View models for agent activity (FR-009, contracts/queries.md "Agent activity").

Pure. Reports, decisions and orchestrator runs come from three queries and are merged here,
newest first, then paged. Each query is asked for `offset + page_size + 1` rows, which is
exactly enough for the merge to be exact.

Rationale and reasoning text is the only text rendered as Markdown, through the restricted
renderer; source titles go through `render.link_or_text`; everything else stays plain text for
the template to escape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from markupsafe import Markup

from trading_agent.dashboard import pages_orders, render, timefmt
from trading_agent.dashboard.pages_orders import Text, text

ANALYSTS = ("research", "opportunistic_identifier")
AGENT_FILTERS = (*ANALYSTS, "portfolio_manager", "runs")
PROBLEM_OUTCOMES = ("failed", "timed_out", "skipped", "interrupted")
COLLAPSE_AT = 300
_OLDEST = datetime.min.replace(tzinfo=UTC)
_RANK = {"report": 2, "decision": 1, "run": 0}


@dataclass(frozen=True)
class Filters:
    agent: str | None = None
    problems: bool = False
    symbol: str | None = None
    day_from: date | None = None
    day_to: date | None = None
    page: int = 1


def parse_filters(
    agent: str | None,
    problems: str | None,
    symbol: str | None,
    day_from: str | None,
    day_to: str | None,
    page: str | None,
) -> Filters:
    """Unknown values fall back to the default; nothing here raises."""
    base = pages_orders.parse_filters(symbol, day_from, day_to, page)
    return Filters(
        agent=agent if agent in AGENT_FILTERS else None,
        problems=problems == "1",
        symbol=base.symbol,
        day_from=base.day_from,
        day_to=base.day_to,
        page=base.page,
    )


def bounds(filters: Filters, zone: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    return pages_orders.bounds(
        pages_orders.Filters(None, filters.day_from, filters.day_to, filters.page), zone
    )


@dataclass(frozen=True)
class Plan:
    """Which queries to run, and for which agent."""

    reports: bool
    decisions: bool
    runs: bool
    report_agent: str | None
    run_agent: str | None


def plan(filters: Filters) -> Plan:
    agent = filters.agent
    runs_only = filters.problems or agent == "runs"
    # A run has no symbol, so a symbol filter can't match one.
    runs = filters.symbol is None
    run_agent = agent if agent in (*ANALYSTS, "portfolio_manager") else None
    if runs_only:
        return Plan(False, False, runs, None, run_agent)
    if agent in ANALYSTS:
        return Plan(True, False, runs, agent, agent)
    if agent == "portfolio_manager":
        return Plan(False, True, runs, None, agent)
    return Plan(True, True, runs, None, None)


# --- the merge ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    kind: str  # "report" | "decision" | "run"
    at: datetime | None
    id: str
    row: Mapping


@dataclass(frozen=True)
class Page:
    items: tuple[Item, ...]
    has_next: bool


def _items(kind: str, rows: Sequence[Mapping], time_of) -> list[Item]:
    return [Item(kind, time_of(row), str(row["id"]), row) for row in rows]


def _sort_key(item: Item) -> tuple:
    return (item.at or _OLDEST, _RANK[item.kind], item.id)


def merge(
    reports: Sequence[Mapping],
    decisions: Sequence[Mapping],
    runs: Sequence[Mapping],
    offset: int,
    size: int,
) -> Page:
    """Newest first across the three lists, then the page at `offset`.

    Ties on time break by kind and then id, so every page boundary is the same on every read.
    Each list must hold at least its first `offset + size + 1` rows (fewer only if it has no
    more); then the page and the next-page flag are exact.
    """
    merged = (
        _items("report", reports, lambda r: r["generated_at"])
        + _items("decision", decisions, lambda r: r["generated_at"])
        + _items("run", runs, lambda r: r["started_at"] or r["slot_at"])
    )
    merged.sort(key=_sort_key, reverse=True)
    return Page(tuple(merged[offset : offset + size]), len(merged) > offset + size)


def missing_report_ids(page: Page) -> list[str]:
    """Cited reports that aren't on this page, whose day a decision's link needs."""
    shown = {item.id for item in page.items if item.kind == "report"}
    cited = {
        str(report_id)
        for item in page.items
        if item.kind == "decision"
        for report_id in item.row["report_ids"]
    }
    return sorted(cited - shown)


# --- the rows -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Body:
    """Markdown text rendered by the restricted renderer, with a plain summary when long."""

    html: Markup
    summary: str | None


def body(source: str) -> Body:
    summary = render.excerpt(source, COLLAPSE_AT) if len(source) > COLLAPSE_AT else None
    return Body(render.markdown(source), summary)


@dataclass(frozen=True)
class SourceView:
    title: Markup
    publisher: str
    published_at: str
    relevance: str


@dataclass(frozen=True)
class ReportView:
    anchor: str
    agent: str
    at: str
    symbol: str
    direction: str
    conviction: str
    size: str
    sources: tuple[SourceView, ...]
    rationale: Body


@dataclass(frozen=True)
class CitedReport:
    label: str
    href: str | None


@dataclass(frozen=True)
class DecisionView:
    anchor: str
    at: str
    symbol: str
    direction: str
    size: str
    quote: str
    cited: tuple[CitedReport, ...]
    reasoning: Body


@dataclass(frozen=True)
class RunView:
    agent: str
    reason: str
    slot: str
    slot_at: str
    started: str
    finished: str
    outcome: str
    problem: bool
    detail: Text | None


@dataclass(frozen=True)
class ActivityRow:
    kind: str
    report: ReportView | None = None
    decision: DecisionView | None = None
    run: RunView | None = None


def _plain(value) -> str:
    shown = f"{value:f}"
    return shown.rstrip("0").rstrip(".") if "." in shown else shown


def _moment(value: datetime | None, zone: ZoneInfo) -> str:
    return "" if value is None else timefmt.moment(value, zone)


def _source(entry: object) -> SourceView | None:
    if not isinstance(entry, Mapping):
        return None
    return SourceView(
        title=render.link_or_text(str(entry.get("title") or "(untitled)"), entry.get("url")),
        publisher=str(entry.get("publisher") or ""),
        published_at=str(entry.get("published_at") or ""),
        relevance=str(entry.get("relevance") or ""),
    )


def _report(row: Mapping, zone: ZoneInfo) -> ReportView:
    sources = row["sources"] if isinstance(row["sources"], list) else []
    return ReportView(
        anchor=f"report-{row['id']}",
        agent=row["agent"],
        at=_moment(row["generated_at"], zone),
        symbol=row["symbol"] or "",
        direction=row["direction"],
        conviction="" if row["conviction"] is None else str(row["conviction"]),
        size="" if row["suggested_size_pct"] is None else f"{_plain(row['suggested_size_pct'])}%",
        sources=tuple(view for view in map(_source, sources) if view is not None),
        rationale=body(row["rationale_md"]),
    )


def _day_link(when: datetime, zone: ZoneInfo) -> str:
    day = when.astimezone(zone).date().isoformat()
    return f"/activity?{urlencode({'from': day, 'to': day})}"


def _cited(
    report_id: object, shown: set[str], times: Mapping[str, datetime], zone: ZoneInfo
) -> CitedReport:
    key = str(report_id)
    label = f"report {key[:8]}"
    if key in shown:
        return CitedReport(label, f"#report-{key}")
    when = times.get(key)
    return CitedReport(label, None if when is None else _day_link(when, zone))


def _decision(
    row: Mapping, shown: set[str], times: Mapping[str, datetime], zone: ZoneInfo
) -> DecisionView:
    quote_time = _moment(row["quote_time"], zone)
    quote = _plain(row["quote_at_decision"]) + (f" at {quote_time}" if quote_time else "")
    return DecisionView(
        anchor=f"decision-{row['id']}",
        at=_moment(row["generated_at"], zone),
        symbol=row["symbol"],
        direction=row["direction"],
        size=f"{_plain(row['size_pct'])}%",
        quote=quote,
        cited=tuple(_cited(i, shown, times, zone) for i in row["report_ids"]),
        reasoning=body(row["reasoning_md"]),
    )


def _run(row: Mapping, zone: ZoneInfo) -> RunView:
    return RunView(
        agent=row["agent"],
        reason=str(row["reason"]).replace("_", " "),
        slot=row["slot_key"] or "",
        slot_at=_moment(row["slot_at"], zone),
        started=_moment(row["started_at"], zone),
        finished=_moment(row["finished_at"], zone),
        outcome=str(row["outcome"]).replace("_", " "),
        problem=row["outcome"] in PROBLEM_OUTCOMES,
        detail=text(row["detail"]) if row["detail"] else None,
    )


@dataclass(frozen=True)
class ActivityView:
    rows: tuple[ActivityRow, ...]
    filters: Filters
    symbol_value: str
    from_value: str
    to_value: str
    prev_url: str | None
    next_url: str | None
    empty_text: str | None
    agents: tuple[tuple[str, str], ...]


AGENT_LABELS = (
    ("", "All"),
    ("research", "Research"),
    ("opportunistic_identifier", "Opportunistic Identifier"),
    ("portfolio_manager", "Portfolio Manager"),
    ("runs", "Runs only"),
)


def _url(filters: Filters, page: int) -> str:
    query: dict[str, str] = {}
    if filters.agent:
        query["agent"] = filters.agent
    if filters.problems:
        query["problems"] = "1"
    if filters.symbol:
        query["symbol"] = filters.symbol
    if filters.day_from:
        query["from"] = filters.day_from.isoformat()
    if filters.day_to:
        query["to"] = filters.day_to.isoformat()
    if page > 1:
        query["page"] = str(page)
    return "/activity" + (f"?{urlencode(query)}" if query else "")


def activity_view(
    page: Page,
    filters: Filters,
    report_times: Mapping[str, datetime],
    zone: ZoneInfo,
) -> ActivityView:
    shown = {item.id for item in page.items if item.kind == "report"}
    rows = []
    for item in page.items:
        if item.kind == "report":
            rows.append(ActivityRow("report", report=_report(item.row, zone)))
        elif item.kind == "decision":
            rows.append(
                ActivityRow("decision", decision=_decision(item.row, shown, report_times, zone))
            )
        else:
            rows.append(ActivityRow("run", run=_run(item.row, zone)))
    return ActivityView(
        rows=tuple(rows),
        filters=filters,
        symbol_value=filters.symbol or "",
        from_value=filters.day_from.isoformat() if filters.day_from else "",
        to_value=filters.day_to.isoformat() if filters.day_to else "",
        prev_url=_url(filters, filters.page - 1) if filters.page > 1 else None,
        next_url=_url(filters, filters.page + 1) if page.has_next else None,
        empty_text=None if rows else "No activity matches.",
        agents=AGENT_LABELS,
    )
