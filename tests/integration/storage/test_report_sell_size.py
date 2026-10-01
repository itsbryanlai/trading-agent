"""Migration 0011: a sell report may suggest 0 (a full exit); an actionable report
may no longer have a null suggested size (specs/007-research-agent research R13)."""

from __future__ import annotations

import pytest

from tests.integration.helpers import sqlstate_of
from tests.integration.storage.test_reports import _INSERT, no_action, report

CHECK_VIOLATION = "23514"


def _state(conn, row):
    return sqlstate_of(conn, _INSERT, row)


@pytest.mark.parametrize("size", [0, 0.001, 100])
def test_a_sell_may_suggest_zero_up_to_100(conn, size):
    assert _state(conn, report(direction="sell", suggested_size_pct=size)) is None


@pytest.mark.parametrize("direction", ["buy", "hold"])
def test_a_buy_or_hold_still_needs_more_than_zero(conn, direction):
    row = report(direction=direction, suggested_size_pct=0)
    assert _state(conn, row) == CHECK_VIOLATION


@pytest.mark.parametrize("direction", ["buy", "sell", "hold"])
def test_an_actionable_report_needs_a_size(conn, direction):
    row = report(direction=direction, suggested_size_pct=None)
    assert _state(conn, row) == CHECK_VIOLATION


@pytest.mark.parametrize(
    ("direction", "size"),
    [("buy", 100.001), ("sell", 100.001), ("hold", 100.001), ("sell", -0.001)],
)
def test_out_of_range_sizes_are_rejected(conn, direction, size):
    assert _state(conn, report(direction=direction, suggested_size_pct=size)) == CHECK_VIOLATION


def test_no_action_still_has_no_size(conn):
    assert _state(conn, no_action()) is None
    assert _state(conn, no_action(suggested_size_pct=0)) == CHECK_VIOLATION


def test_the_constraint_keeps_its_name(conn):
    row = conn.execute(
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        "WHERE conname = 'reports_suggested_size_range'"
    ).fetchone()
    assert row is not None
    assert "sell" in row["definition"]
