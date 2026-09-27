"""US7: status mapping and fill arithmetic (research E7, E8)."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from trading_agent.execution.fills import (
    Holding,
    UnexpectedStatus,
    apply_fill,
    fill_delta,
    is_final,
    map_status,
)

WORKING = [
    "new",
    "accepted",
    "pending_new",
    "accepted_for_bidding",
    "held",
    "calculated",
    "stopped",
    "suspended",
    "pending_cancel",
    "pending_replace",
]


@pytest.mark.parametrize("raw", WORKING)
def test_working_statuses_are_submitted_or_partially_filled(raw):
    assert map_status(raw, D(0)) == "submitted"
    assert map_status(raw, D(3)) == "partially_filled"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("partially_filled", "partially_filled"),
        ("filled", "filled"),
        ("done_for_day", "expired"),
        ("expired", "expired"),
        ("canceled", "canceled"),
        ("rejected", "rejected"),
    ],
)
def test_direct_mappings(raw, expected):
    assert map_status(raw, D(1)) == expected


@pytest.mark.parametrize("raw", ["replaced", "something_new"])
def test_statuses_execution_never_causes_are_unexpected(raw):
    with pytest.raises(UnexpectedStatus):
        map_status(raw, D(0))


def test_final_states():
    assert {
        s
        for s in ["submitted", "partially_filled", "filled", "rejected", "canceled", "expired"]
        if is_final(s)
    } == {"filled", "rejected", "canceled", "expired"}


def test_cumulative_readings_become_the_incremental_fill():
    assert fill_delta(D(24), D(200), D(30), D(202)) == (D(6), D(210))
    assert fill_delta(D(0), None, D(10), D("201.5")) == (D(10), D("201.5"))
    assert fill_delta(D(10), D(200), D(10), D(200)) is None


def test_buys_average_and_sells_keep_the_entry_price():
    assert apply_fill(None, "buy", D(24), D("201.50")) == Holding(D(24), D("201.50"))
    assert apply_fill(Holding(D(24), D(200)), "buy", D(6), D(210)) == Holding(D(30), D(202))
    assert apply_fill(Holding(D(50), D(200)), "sell", D(20), D(190)) == Holding(D(30), D(200))
    assert apply_fill(Holding(D(30), D(200)), "sell", D(30), D(190)) is None


def test_impossible_sells_are_errors_not_negative_positions():
    with pytest.raises(ValueError):
        apply_fill(None, "sell", D(1), D(1))
    with pytest.raises(ValueError):
        apply_fill(Holding(D(5), D(1)), "sell", D(6), D(1))
