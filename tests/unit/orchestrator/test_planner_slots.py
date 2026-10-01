"""Slots and the cutoff (research O9): trading days only, ET wall-clock, early closes."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.unit.orchestrator.support import (
    EARLY_CLOSE,
    MONDAY,
    SATURDAY,
    THANKSGIVING,
    WINTER,
    config,
    et,
    state,
)
from trading_agent.orchestrator import planner as p


def _times(slots):
    return [when for _, when in slots]


def test_normal_day():
    cfg = config()
    assert p.research_slots(MONDAY, cfg) == [("research_daily", et("08:30"))]
    oi = p.oi_slots(MONDAY, cfg)
    assert [k for k, _ in oi] == [f"oi@{h}:00" for h in range(10, 16)]
    assert _times(oi) == [et(f"{h}:00") for h in range(10, 16)]
    assert p.cutoff(MONDAY, cfg) == et("15:30")


def test_optional_intraday_research():
    cfg = config(research={"interval": timedelta(minutes=120)})
    slots = p.research_slots(MONDAY, cfg)
    assert slots[0] == ("research_daily", et("08:30"))
    assert _times(slots[1:]) == [et("10:30"), et("12:30"), et("14:30")]
    assert slots[1][0] == "research@10:30"


def test_early_close():
    cfg = config()
    assert p.cutoff(EARLY_CLOSE, cfg) == et("12:30", EARLY_CLOSE)
    assert _times(p.oi_slots(EARLY_CLOSE, cfg)) == [
        et("10:00", EARLY_CLOSE),
        et("11:00", EARLY_CLOSE),
        et("12:00", EARLY_CLOSE),
    ]


def test_winter_keeps_et_wall_clock():
    cfg = config()
    assert p.research_slots(WINTER, cfg)[0][1] == et("08:30", WINTER)
    assert et("08:30", WINTER).hour == 13  # EST: UTC-5
    assert et("08:30").hour == 12  # EDT: UTC-4


@pytest.mark.parametrize("day", [SATURDAY, THANKSGIVING])
def test_no_slots_on_non_trading_days(day):
    cfg = config()
    assert p.research_slots(day, cfg) == []
    assert p.oi_slots(day, cfg) == []
    assert p.cutoff(day, cfg) is None
    assert p.plan(et("10:00", day), cfg, state(day=day)) == []


def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        p.plan(datetime(2026, 9, 28, 14), config(), state())
