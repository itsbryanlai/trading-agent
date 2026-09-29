"""The day's symbol set: who qualifies (FR-001), what's skipped (FR-003), and the order (D8)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_agent.reference.normalize import INVALID_SYMBOL, Failure
from trading_agent.reference.symbols import (
    Candidate,
    build_symbol_set,
    is_plausible_ticker,
    window_start,
)

NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)  # Monday 08:30 ET
PREVIOUS_OPEN = datetime(2026, 9, 25, 13, 30, tzinfo=UTC)  # Friday's open


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def position(symbol):
    return Candidate(symbol, "position", None, None)


def report(symbol, named_at, active_until):
    return Candidate(symbol, "report", named_at, active_until)


def decision(symbol, named_at):
    return Candidate(symbol, "decision", named_at, None)


@pytest.mark.parametrize("symbol", ["AAPL", "A", "BRK.B", "BF-B", "GOOGL"])
def test_plausible_tickers(symbol):
    assert is_plausible_ticker(symbol)


@pytest.mark.parametrize(
    "symbol", ["aapl", "AAPLXX", "BRK.", "BRK.BBB", "A B", "", "$AAPL", "1234", "AAPL\n", None]
)
def test_implausible_tickers(symbol):
    assert not is_plausible_ticker(symbol)


def test_window_starts_at_the_previous_sessions_open():
    assert window_start(NOW) == PREVIOUS_OPEN
    # After a holiday: Friday 2026-11-27's window opens Wednesday 2026-11-25.
    assert window_start(utc(2026, 11, 27, 13)) == utc(2026, 11, 25, 14, 30)


def test_who_qualifies():
    candidates = [
        position("HELD"),
        report("OLDAC", utc(2026, 8, 1, 14), utc(2026, 12, 1)),  # old but still active
        report("OLDEX", utc(2026, 9, 24, 14), utc(2026, 9, 24, 20)),  # expired, before window
        report("RECNT", utc(2026, 9, 25, 14), utc(2026, 9, 25, 20)),  # expired, in window
        decision("ATOPN", PREVIOUS_OPEN),  # exactly at the window start
        decision("BEFOR", utc(2026, 9, 25, 13, 29)),  # a minute before
    ]
    result = build_symbol_set(candidates, ("SEED",), NOW)
    assert set(result.ordered) == {"HELD", "OLDAC", "RECNT", "ATOPN", "SEED"}


def test_each_symbol_once_and_implausible_ones_skipped_once():
    candidates = [
        position("AAPL"),
        report("AAPL", utc(2026, 9, 28, 11), utc(2026, 9, 28, 20)),
        decision("bad$", utc(2026, 9, 28, 11)),
        report("bad$", utc(2026, 9, 28, 11), utc(2026, 9, 28, 20)),
    ]
    result = build_symbol_set(candidates, ("AAPL",), NOW)
    assert result.ordered == ["AAPL"]
    assert result.skipped == [Failure("bad$", INVALID_SYMBOL)]


def test_order_most_likely_to_be_bought_first():
    candidates = [
        position("ZHELD"),
        position("AHELD"),
        report("ACTIV", utc(2026, 9, 25, 15), utc(2026, 9, 28, 20)),  # still active
        decision("TODAY", utc(2026, 9, 28, 12)),  # decision made today
        decision("FRI", utc(2026, 9, 25, 15)),  # recent, not today
        report("FRIRP", utc(2026, 9, 25, 15), utc(2026, 9, 25, 20)),  # recent, expired
    ]
    result = build_symbol_set(candidates, ("SEEDB", "SEEDA"), NOW)
    assert result.ordered == [
        "ACTIV",
        "TODAY",
        "AHELD",
        "ZHELD",
        "FRI",
        "FRIRP",
        "SEEDA",
        "SEEDB",
    ]


def test_a_symbol_takes_its_highest_priority():
    candidates = [position("BOTH"), report("BOTH", utc(2026, 9, 28, 11), utc(2026, 9, 28, 20))]
    result = build_symbol_set(candidates + [position("AAA")], (), NOW)
    assert result.ordered == ["BOTH", "AAA"]


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        build_symbol_set([], (), datetime(2026, 9, 28, 12))
