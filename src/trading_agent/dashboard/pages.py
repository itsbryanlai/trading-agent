"""View models for the state banner and the overview (FR-006, FR-011).

Pure: rows and times come in as arguments, display strings and chart geometry go out. The
start of a pause is `trading_paused_changed_at` or nothing: never `updated_at`, which the
Risk Gate rewrites every trading day.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading_agent.dashboard import charts, timefmt

NOT_RECORDED = "start time not recorded"
NO_SNAPSHOT = "No account snapshot yet."
NO_CHART_DATA = "No data for this range."
HALT_TEXT = "Daily-loss halt active: no new orders until the next trading day."


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: str
    avg_entry_price: str
    cost: str


@dataclass(frozen=True)
class SnapshotView:
    taken_at: str
    age: str
    equity: str
    cash: str
    buying_power: str


@dataclass(frozen=True)
class OverviewView:
    state_text: str
    halt_text: str | None
    positions: tuple[PositionView, ...]
    snapshot: SnapshotView | None
    no_snapshot_text: str | None
    chart: charts.Chart
    chart_first: str | None
    chart_last: str | None
    chart_empty_text: str | None


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _quantity(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _since(state: Mapping, zone: ZoneInfo) -> str | None:
    changed = state.get("trading_paused_changed_at")
    return None if changed is None else timefmt.moment(changed, zone)


def banner(
    state: Mapping, run_while_paused: bool | None, now: datetime, zone: ZoneInfo
) -> str | None:
    """The observe-only banner while paused (FR-011), else None."""
    if not state["trading_paused"]:
        return None
    since = _since(state, zone)
    paused = f"Trading paused since {since}." if since else f"Trading paused ({NOT_RECORDED})."
    if run_while_paused is True:
        pm = "The PM still runs (run_while_paused: true), and the gate rejects every buy."
    elif run_while_paused is False:
        pm = (
            "The PM does not run while paused (run_while_paused: false), "
            "and the gate rejects every buy."
        )
    else:
        pm = (
            "The deployed run_while_paused setting could not be read, "
            "and the gate rejects every buy."
        )
    return f"Observe-only. {paused} {pm}"


def halt_line(state: Mapping) -> str | None:
    return HALT_TEXT if state["daily_loss_halt_active"] else None


def state_text(state: Mapping, zone: ZoneInfo) -> str:
    if not state["trading_paused"]:
        return "Trading is running."
    since = _since(state, zone)
    return f"Trading is paused since {since}." if since else f"Trading is paused ({NOT_RECORDED})."


def _snapshot_view(snapshot: Mapping, now: datetime, zone: ZoneInfo) -> SnapshotView:
    return SnapshotView(
        taken_at=timefmt.moment(snapshot["taken_at"], zone),
        age=timefmt.age(now, snapshot["taken_at"]),
        equity=money(snapshot["equity"]),
        cash=money(snapshot["cash"]),
        buying_power=money(snapshot["buying_power"]),
    )


def overview(
    state: Mapping,
    positions: Sequence[Mapping],
    snapshot: Mapping | None,
    points: Sequence[Mapping],
    now: datetime,
    zone: ZoneInfo,
) -> OverviewView:
    chart = charts.polyline([(row["taken_at"], row["equity"]) for row in points])
    return OverviewView(
        state_text=state_text(state, zone),
        halt_text=halt_line(state),
        positions=tuple(
            PositionView(
                p["symbol"],
                _quantity(p["qty"]),
                money(p["avg_entry_price"]),
                money(p["qty"] * p["avg_entry_price"]),
            )
            for p in positions
        ),
        snapshot=None if snapshot is None else _snapshot_view(snapshot, now, zone),
        no_snapshot_text=NO_SNAPSHOT if snapshot is None else None,
        chart=chart,
        chart_first=None if chart.first_at is None else timefmt.moment(chart.first_at, zone),
        chart_last=None if chart.last_at is None else timefmt.moment(chart.last_at, zone),
        chart_empty_text=NO_CHART_DATA if chart.empty else None,
    )
