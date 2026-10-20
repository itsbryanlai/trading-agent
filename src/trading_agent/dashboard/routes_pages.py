"""The signed-in pages and the health check (contracts/routes.md).

`shell` builds what every signed-in page shares (the banner, the halt line, the toggle button,
the sign-out form's CSRF value and the "Read at" footer). Each page reads its data inside one
`store.read()` transaction and passes the state row to `shell`.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse

from trading_agent.dashboard import (
    charts,
    deployed,
    pages,
    pages_activity,
    pages_journal,
    pages_orders,
    timefmt,
)
from trading_agent.dashboard.deps import Deps, SessionDep, get_deps, render
from trading_agent.dashboard.store import StoreError
from trading_agent.risk import calendar

router = APIRouter()

RANGES = ("1d", "1w", "1m", "all")
DEFAULT_RANGE = "1w"
DAILY_RANGES = ("1m", "all")  # one point per trading day
HEALTH_CACHE = timedelta(seconds=10)


class HealthCache:
    """`SELECT 1` at most once per 10 s, single-flight under a lock (research D12).

    A caller that finds a check running does not wait: it gets the cached answer (unavailable
    if there is none yet). So a flood of unauthenticated requests opens at most one connection
    per 10 seconds and never queues behind it.
    """

    def __init__(self, store, clock) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._ok = False
        self._at: datetime | None = None

    def ok(self) -> bool:
        # Never wait: with a check already running (its statement timeout is 10 s), answer from
        # the cache, or "unavailable" if there is none yet, so a flood cannot hold the thread
        # pool behind one slow query.
        if not self._lock.acquire(blocking=False):
            return self._ok if self._at is not None else False
        try:
            now = self._clock()
            if self._at is not None and now - self._at < HEALTH_CACHE:
                return self._ok
            try:
                with self._store.read() as reader:
                    self._ok = bool(reader.ping())
            except Exception:  # any failure is "unavailable"; the answer carries no detail
                self._ok = False
            self._at = now
            return self._ok
        finally:
            self._lock.release()


def shell(deps: Deps, session, state: dict, now: datetime, active: str) -> dict:
    """The context every signed-in page shares."""
    run_while_paused = deployed.run_while_paused(deps.config_dir)
    paused = state["trading_paused"]
    return {
        "signed_in": True,
        "csrf": session.csrf,
        "active": active,
        "banner": pages.banner(state, run_while_paused, now, deps.zone),
        "halt": pages.halt_line(state),
        "toggle_label": "Resume trading" if paused else "Pause trading",
        "read_at": timefmt.moment(now, deps.zone),
    }


def _since(name: str, now: datetime) -> datetime | None:
    return charts.range_start(
        name, now, is_session=calendar.is_session, open_time=calendar.open_time
    )


@router.get("/")
def overview(
    request: Request,
    session: SessionDep,
    range_name: Annotated[str, Query(alias="range")] = DEFAULT_RANGE,
) -> Response:
    deps = get_deps(request)
    name = range_name if range_name in RANGES else DEFAULT_RANGE
    now = deps.clock()
    with deps.store.read() as reader:
        state = reader.state()
        if state is None:
            raise StoreError("database: NoStateRow")
        positions = reader.positions()
        snapshot = reader.latest_snapshot()
        points = reader.equity_points(_since(name, now), name in DAILY_RANGES)
    view = pages.overview(state, positions, snapshot, points, now, deps.zone)
    context = shell(deps, session, state, now, "overview")
    return render(deps, "overview.html", view=view, ranges=RANGES, range_name=name, **context)


@router.get("/healthz")
def healthz(request: Request) -> Response:
    ok = request.app.state.health.ok()
    return PlainTextResponse("ok" if ok else "unavailable", status_code=200 if ok else 503)


@router.get("/orders")
def orders(
    request: Request,
    session: SessionDep,
    symbol: Annotated[str | None, Query()] = None,
    day_from: Annotated[str | None, Query(alias="from")] = None,
    day_to: Annotated[str | None, Query(alias="to")] = None,
    page: Annotated[str | None, Query()] = None,
) -> Response:
    deps = get_deps(request)
    filters = pages_orders.parse_filters(symbol, day_from, day_to, page)
    since, until = pages_orders.bounds(filters, deps.zone)
    size = deps.config.page_size
    now = deps.clock()
    with deps.store.read() as reader:
        state = reader.state()
        if state is None:
            raise StoreError("database: NoStateRow")
        rows = reader.orders(filters.symbol, since, until, size + 1, (filters.page - 1) * size)
    view = pages_orders.orders_view(rows, filters, size, deps.zone)
    return render(deps, "orders.html", view=view, **shell(deps, session, state, now, "orders"))


@router.get("/activity")
def activity(
    request: Request,
    session: SessionDep,
    agent: Annotated[str | None, Query()] = None,
    problems: Annotated[str | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    day_from: Annotated[str | None, Query(alias="from")] = None,
    day_to: Annotated[str | None, Query(alias="to")] = None,
    page: Annotated[str | None, Query()] = None,
) -> Response:
    deps = get_deps(request)
    filters = pages_activity.parse_filters(agent, problems, symbol, day_from, day_to, page)
    since, until = pages_activity.bounds(filters, deps.zone)
    plan = pages_activity.plan(filters)
    size = deps.config.page_size
    offset = (filters.page - 1) * size
    limit = offset + size + 1
    now = deps.clock()
    with deps.store.read() as reader:
        state = reader.state()
        if state is None:
            raise StoreError("database: NoStateRow")
        reports = (
            reader.reports(plan.report_agent, filters.symbol, since, until, limit)
            if plan.reports
            else []
        )
        decisions = reader.decisions(filters.symbol, since, until, limit) if plan.decisions else []
        runs = (
            reader.runs(plan.run_agent, since, until, filters.problems, limit) if plan.runs else []
        )
        merged = pages_activity.merge(reports, decisions, runs, offset, size)
        times = reader.report_times(pages_activity.missing_report_ids(merged))
    view = pages_activity.activity_view(merged, filters, times, deps.zone)
    context = shell(deps, session, state, now, "activity")
    return render(deps, "activity.html", view=view, **context)


@router.get("/journal")
def journal(
    request: Request,
    session: SessionDep,
    range_name: Annotated[str | None, Query(alias="range")] = None,
    page: Annotated[str | None, Query()] = None,
) -> Response:
    deps = get_deps(request)
    name, number = pages_journal.parse_query(range_name, page)
    size = deps.config.page_size
    now = deps.clock()
    since = pages_journal.since_day(name, now)
    with deps.store.read() as reader:
        state = reader.state()
        if state is None:
            raise StoreError("database: NoStateRow")
        page_rows = reader.journal_page(since, size + 1, (number - 1) * size)
        range_rows = reader.journal_range(since)
    view = pages_journal.journal_view(page_rows, range_rows, name, number, size)
    return render(deps, "journal.html", view=view, **shell(deps, session, state, now, "journal"))
