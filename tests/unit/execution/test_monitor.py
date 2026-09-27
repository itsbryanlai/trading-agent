"""US5: the stop-loss scan (FR-014, research E9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from trading_agent.execution.broker import Trade
from trading_agent.execution.fills import Holding
from trading_agent.execution.monitor import MAX_TRADE_AGE, scan, stop_line

NOW = datetime(2026, 9, 28, 14, 10, tzinfo=UTC)
HELD = {"AAPL": Holding(D(50), D(200))}


def trade(price, age=timedelta(0), symbol="AAPL"):
    return Trade(symbol, D(str(price)), NOW - age)


def test_the_line_is_twenty_percent_under_the_entry():
    assert stop_line(D(200), D(20)) == D(160)


def test_at_the_line_is_a_breach_and_a_cent_above_is_not():
    result = scan(HELD, {"AAPL": trade(160)}, D(20), NOW)
    assert [(b.symbol, b.price) for b in result.breaches] == [("AAPL", D(160))]
    assert result.complete
    assert scan(HELD, {"AAPL": trade("160.01")}, D(20), NOW).breaches == ()
    assert scan(HELD, {"AAPL": trade(161)}, D(20), NOW).breaches == ()


def test_a_stale_trade_is_skipped_and_leaves_the_scan_incomplete():
    result = scan(HELD, {"AAPL": trade(150, MAX_TRADE_AGE + timedelta(seconds=1))}, D(20), NOW)
    assert result.breaches == () and result.stale == ("AAPL",) and not result.complete
    result = scan(HELD, {"AAPL": trade(150, MAX_TRADE_AGE)}, D(20), NOW)
    assert len(result.breaches) == 1


def test_a_missing_trade_is_reported_as_failed():
    result = scan(HELD, {"AAPL": None}, D(20), NOW)
    assert result.failed == ("AAPL",) and not result.complete


def test_a_symbol_with_an_exit_already_on_its_way_is_skipped():
    result = scan(HELD, {"AAPL": trade(150)}, D(20), NOW, skip=frozenset({"AAPL"}))
    assert result.breaches == () and result.complete


def test_every_held_symbol_is_judged_independently():
    held = {"AAPL": Holding(D(50), D(200)), "MSFT": Holding(D(10), D(400))}
    trades = {"AAPL": trade(170), "MSFT": trade(300, symbol="MSFT")}
    result = scan(held, trades, D(20), NOW)
    assert [b.symbol for b in result.breaches] == ["MSFT"]
