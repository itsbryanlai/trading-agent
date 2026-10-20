"""View models for the state, banner and overview (specs/014 tasks T016; FR-006, FR-011)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading_agent.dashboard import pages

SGT = ZoneInfo("Asia/Singapore")
DEFAULT = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)  # Tue 07:00 +08
WEEKEND = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)
SINCE = datetime(2026, 10, 5, 21, 12, tzinfo=UTC)  # 6 Oct 05:12 SGT


def _state(**kwargs) -> dict:
    return {
        "trading_paused": False,
        "trading_paused_changed_at": None,
        "daily_loss_halt_active": False,
        "current_trading_date": date(2026, 10, 19),
        **kwargs,
    }


def _snapshot(taken_at=datetime(2026, 10, 19, 20, 0, tzinfo=UTC), equity="100000.00") -> dict:
    return {
        "taken_at": taken_at,
        "equity": Decimal(equity),
        "cash": Decimal("40000.5"),
        "buying_power": Decimal("80000"),
    }


# --- banner (FR-011) ------------------------------------------------------------------------


def test_no_banner_while_running():
    for setting in (True, False, None):
        assert pages.banner(_state(), setting, DEFAULT, SGT) is None


def test_the_banner_says_paused_since_when_and_that_the_pm_still_runs():
    state = _state(trading_paused=True, trading_paused_changed_at=SINCE)
    assert pages.banner(state, True, DEFAULT, SGT) == (
        "Observe-only. Trading paused since 6 Oct 05:12 SGT (5 Oct 17:12 ET). "
        "The PM still runs (run_while_paused: true), and the gate rejects every buy."
    )


def test_the_banner_when_the_schedule_stops_the_pm_while_paused():
    state = _state(trading_paused=True, trading_paused_changed_at=SINCE)
    text = pages.banner(state, False, DEFAULT, SGT)
    assert "The PM does not run while paused (run_while_paused: false)" in text
    assert text.endswith("the gate rejects every buy.")


def test_the_banner_when_the_setting_could_not_be_read():
    state = _state(trading_paused=True, trading_paused_changed_at=SINCE)
    text = pages.banner(state, None, DEFAULT, SGT)
    assert "The deployed run_while_paused setting could not be read" in text
    assert "the gate rejects every buy" in text


def test_a_null_start_time_reads_not_recorded_and_never_updated_at():
    state = _state(
        trading_paused=True,
        updated_at=datetime(2026, 10, 19, 13, 30, tzinfo=UTC),
    )
    text = pages.banner(state, True, DEFAULT, SGT)
    assert "start time not recorded" in text
    assert "19 Oct" not in text and "since" not in text


@pytest.mark.parametrize("setting", [True, False, None])
def test_every_banner_says_the_gate_rejects_buys(setting):
    state = _state(trading_paused=True, trading_paused_changed_at=SINCE)
    assert "the gate rejects every buy" in pages.banner(state, setting, DEFAULT, SGT)


# --- halt -----------------------------------------------------------------------------------


def test_halt_line():
    assert pages.halt_line(_state()) is None
    assert pages.halt_line(_state(daily_loss_halt_active=True)) == (
        "Daily-loss halt active: no new orders until the next trading day."
    )


# --- overview -------------------------------------------------------------------------------


def _overview(**kwargs):
    args = {
        "state": _state(),
        "positions": [],
        "snapshot": _snapshot(),
        "points": [],
        "now": DEFAULT,
        "zone": SGT,
    }
    args.update(kwargs)
    return pages.overview(**args)


def test_state_text_running_and_paused():
    assert _overview().state_text == "Trading is running."
    paused = _overview(state=_state(trading_paused=True, trading_paused_changed_at=SINCE))
    assert paused.state_text == "Trading is paused since 6 Oct 05:12 SGT (5 Oct 17:12 ET)."
    unknown = _overview(state=_state(trading_paused=True))
    assert unknown.state_text == "Trading is paused (start time not recorded)."


def test_the_halt_is_in_the_overview():
    assert _overview(state=_state(daily_loss_halt_active=True)).halt_text
    assert _overview().halt_text is None


def test_positions_carry_cost_as_quantity_times_average_entry_price():
    rows = [
        {"symbol": "AAPL", "qty": Decimal("10"), "avg_entry_price": Decimal("187.25")},
        {"symbol": "NVDA", "qty": Decimal("2.5"), "avg_entry_price": Decimal("100.10")},
    ]
    view = _overview(positions=rows)
    assert [(p.symbol, p.qty, p.avg_entry_price, p.cost) for p in view.positions] == [
        ("AAPL", "10", "187.25", "1,872.50"),
        ("NVDA", "2.5", "100.10", "250.25"),
    ]


def test_no_positions():
    assert _overview(positions=[]).positions == ()


def test_cash_equity_time_and_age():
    view = _overview()
    snap = view.snapshot
    assert snap.equity == "100,000.00" and snap.cash == "40,000.50"
    assert snap.buying_power == "80,000.00"
    assert snap.taken_at == "20 Oct 04:00 SGT (19 Oct 16:00 ET)"
    assert snap.age == "3 h old"


def test_no_snapshot_says_so_plainly():
    view = _overview(snapshot=None)
    assert view.snapshot is None
    assert view.no_snapshot_text == "No account snapshot yet."


def test_a_weekend_snapshot_shows_its_age():
    old = _snapshot(taken_at=datetime(2026, 10, 23, 1, 30, tzinfo=UTC))
    assert _overview(snapshot=old, now=WEEKEND).snapshot.age == "2 d old"


def test_the_chart_is_built_from_the_points():
    points = [
        {"taken_at": datetime(2026, 10, 19, 14, 0, tzinfo=UTC), "equity": Decimal("100")},
        {"taken_at": datetime(2026, 10, 19, 20, 0, tzinfo=UTC), "equity": Decimal("110")},
    ]
    view = _overview(points=points)
    assert len(view.chart.paths) == 1
    assert (view.chart.min_label, view.chart.max_label) == ("100.00", "110.00")
    assert view.chart_first == "19 Oct 22:00 SGT (19 Oct 10:00 ET)"
    assert view.chart_last == "20 Oct 04:00 SGT (19 Oct 16:00 ET)"
    assert view.chart_empty_text is None


def test_one_point_is_a_dot_and_none_says_no_data():
    one = _overview(points=[{"taken_at": DEFAULT, "equity": Decimal("100")}])
    assert one.chart.dots and not one.chart.paths and one.chart_empty_text is None
    none = _overview(points=[])
    assert none.chart.empty and none.chart_empty_text == "No data for this range."
    assert none.chart_first is None and none.chart_last is None


def test_no_data_at_all():
    view = _overview(snapshot=None, positions=[], points=[])
    assert view.no_snapshot_text and view.chart_empty_text and view.positions == ()
