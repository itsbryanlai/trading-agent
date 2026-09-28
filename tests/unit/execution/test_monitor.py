"""US5: the stop-loss scan (FR-014, research E9, ADR 0014)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from trading_agent.execution.broker import Quote, Trade
from trading_agent.execution.fills import Holding
from trading_agent.execution.monitor import MAX_TRADE_AGE, scan, stop_line

NOW = datetime(2026, 9, 28, 14, 10, tzinfo=UTC)
HELD = {"AAPL": Holding(D(50), D(200))}


def trade(price, age=timedelta(0), symbol="AAPL"):
    return Trade(symbol, D(str(price)), NOW - age)


def bid(price, age=timedelta(0), symbol="AAPL"):
    return Quote(symbol, D(str(price)) + 1, NOW - age, bid=D(str(price)))


def run(held, trades, quotes=None, skip=frozenset()):
    """Scan with each symbol's bid equal to its last trade unless `quotes` is given."""
    if quotes is None:
        quotes = {s: (bid(t.price, symbol=s) if t else None) for s, t in trades.items()}
    return scan(held, trades, quotes, D(20), NOW, skip)


def test_the_line_is_twenty_percent_under_the_entry():
    assert stop_line(D(200), D(20)) == D(160)


def test_at_the_line_is_a_breach_and_a_cent_above_is_not():
    result = run(HELD, {"AAPL": trade(160)})
    assert [(b.symbol, b.price) for b in result.breaches] == [("AAPL", D(160))]
    assert result.complete
    assert run(HELD, {"AAPL": trade("160.01")}).breaches == ()
    assert run(HELD, {"AAPL": trade(161)}).breaches == ()


def test_a_stale_trade_is_skipped_and_leaves_the_scan_incomplete():
    result = run(HELD, {"AAPL": trade(150, MAX_TRADE_AGE + timedelta(seconds=1))})
    assert result.breaches == () and result.stale == ("AAPL",) and not result.complete
    assert len(run(HELD, {"AAPL": trade(150, MAX_TRADE_AGE)}).breaches) == 1


def test_a_missing_trade_is_reported_as_failed():
    result = run(HELD, {"AAPL": None})
    assert result.failed == ("AAPL",) and not result.complete


def test_a_symbol_with_an_exit_already_on_its_way_is_skipped():
    result = run(HELD, {"AAPL": trade(150)}, skip=frozenset({"AAPL"}))
    assert result.breaches == () and result.complete


def test_every_held_symbol_is_judged_independently():
    held = {"AAPL": Holding(D(50), D(200)), "MSFT": Holding(D(10), D(400))}
    result = run(held, {"AAPL": trade(170), "MSFT": trade(300, symbol="MSFT")})
    assert [b.symbol for b in result.breaches] == ["MSFT"]


def test_a_trade_through_the_line_needs_the_bid_to_confirm_it():
    # ADR 0014: one odd print below the line isn't enough.
    unconfirmed = run(HELD, {"AAPL": trade(150)}, {"AAPL": bid(161)})
    assert unconfirmed.breaches == () and unconfirmed.complete
    confirmed = run(HELD, {"AAPL": trade(150)}, {"AAPL": bid(160)})
    assert [b.price for b in confirmed.breaches] == [D(150)]  # records the trade price


def test_a_missing_zero_or_stale_bid_is_a_failed_check():
    for quote in (None, bid(0), bid(150, age=timedelta(seconds=61))):
        result = run(HELD, {"AAPL": trade(150)}, {"AAPL": quote})
        assert result.breaches == () and result.failed == ("AAPL",) and not result.complete


def test_no_bid_is_needed_when_the_trade_is_above_the_line():
    result = run(HELD, {"AAPL": trade(170)}, {"AAPL": None})
    assert result.breaches == () and result.complete
