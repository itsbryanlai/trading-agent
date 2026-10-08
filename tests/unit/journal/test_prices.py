"""Fetching and accepting closing prices (research J2, J8)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.fakes.market_data import FakeMarketData
from trading_agent.journal.config import JournalConfig
from trading_agent.journal.model import Price
from trading_agent.journal.prices import SYMBOL_PATTERN, fetch_closes
from trading_agent.reference.provider import (
    KeyRejected,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)

DAY = date(2026, 10, 9)  # EDT: open 13:30 UTC, close 20:00 UTC
EARLY = date(2026, 11, 27)  # EST, early close 13:00 ET = 18:00 UTC
CFG = JournalConfig(
    holding_sessions=5,
    finnhub_calls_per_minute=20,
    fetch_deadline_seconds=480,
    close_grace_minutes=5,
)  # one call every 3.0 s


def at(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


class Clock:
    """A fake clock that only moves when `sleep` is called."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def market(clock):
    return FakeMarketData(clock=clock.monotonic, quote_time=at(19, 59))


def fetch(market, clock, symbols, day=DAY, cfg=CFG):
    return fetch_closes(market, symbols, day, cfg, sleep=clock.sleep, monotonic=clock.monotonic)


def test_a_quote_inside_the_session_is_the_close(market, clock):
    market.add("AAPL", current="229.15")
    assert fetch(market, clock, ["AAPL"]) == {"AAPL": Price("AAPL", Decimal("229.15"), None)}


@pytest.mark.parametrize(
    ("when", "ok"),
    [
        (at(13, 29), False),  # before the open
        (at(13, 30), True),  # the open itself
        (at(15, 0), True),  # an illiquid name's last mid-afternoon trade
        (at(20, 0), True),
        (at(20, 5), True),  # the closing auction's prints, inside the grace
        (at(20, 6), False),  # after the close plus grace: after-hours, refused
        (at(22, 0), False),
        (datetime(2026, 10, 8, 19, 59, tzinfo=UTC), False),  # yesterday's
    ],
)
def test_the_trade_time_must_fall_in_the_session_plus_grace(market, clock, when, ok):
    market.add("AAPL", quote_time=when)
    price = fetch(market, clock, ["AAPL"])["AAPL"]
    assert (price.close is not None) is ok
    expected = None if ok else ("stale" if when < at(13, 30) else "after_close")
    assert price.reason == expected


def test_the_early_close_day_uses_the_early_close(market, clock):
    market.add("AAPL", quote_time=at(18, 3, EARLY))
    market.add("MSFT", quote_time=at(19, 0, EARLY))  # a normal day's afternoon: after-hours
    got = fetch(market, clock, ["AAPL", "MSFT"], day=EARLY)
    assert got["AAPL"].close is not None
    assert got["MSFT"] == Price("MSFT", None, "after_close")


def test_the_grace_is_configurable(market, clock):
    cfg = JournalConfig(5, 20, 480, 0)
    market.add("AAPL", quote_time=at(20, 1))
    assert fetch(market, clock, ["AAPL"], cfg=cfg)["AAPL"].reason == "after_close"


def test_no_current_price_is_no_price(market, clock):
    market.add("AAPL", current=None)
    assert fetch(market, clock, ["AAPL"])["AAPL"] == Price("AAPL", None, "no_price")


def test_a_price_with_no_trade_time_is_stale(market, clock):
    market.add("AAPL", quote_time=None)
    assert fetch(market, clock, ["AAPL"])["AAPL"] == Price("AAPL", None, "stale")


def test_a_zero_or_negative_price_is_unusable(market, clock):
    market.add("AAPL", current="0")
    market.add("MSFT", current="-1")
    got = fetch(market, clock, ["AAPL", "MSFT"])
    assert got["AAPL"].reason == "no_price"
    assert got["MSFT"].reason == "no_price"


def test_results_are_in_symbol_order_and_each_symbol_is_fetched_once(market, clock):
    for symbol in ("MSFT", "AAPL", "NVDA"):
        market.add(symbol)
    got = fetch(market, clock, ["NVDA", "MSFT", "AAPL", "MSFT"])
    assert list(got) == ["AAPL", "MSFT", "NVDA"]
    assert [s for _, _, s in market.calls] == ["AAPL", "MSFT", "NVDA"]


def test_calls_are_paced_at_the_configured_rate(market, clock):
    for symbol in ("AAPL", "MSFT", "NVDA"):
        market.add(symbol)
    fetch(market, clock, ["AAPL", "MSFT", "NVDA"])
    assert [t for t, _, _ in market.calls] == [0.0, 3.0, 6.0]


class Flaky(FakeMarketData):
    """Raises `error` for the first `times` calls for `symbol`, then answers."""

    def __init__(self, symbol, error, times, **kw):
        super().__init__(**kw)
        self._flaky = (symbol, error, times)

    def get_quote(self, symbol):
        name, error, times = self._flaky
        if symbol == name and self.calls_for(symbol).count("get_quote") < times:
            self._record("get_quote", symbol)
            raise error
        return super().get_quote(symbol)


@pytest.mark.parametrize("error", [RateLimited("429"), ProviderUnavailable("boom")])
def test_a_transient_error_is_retried_one_interval_later(clock, error):
    market = Flaky("AAPL", error, 2, clock=clock.monotonic, quote_time=at(19, 59))
    market.add("AAPL")
    market.add("MSFT")
    got = fetch(market, clock, ["AAPL", "MSFT"])
    assert got["AAPL"].close is not None
    assert [(t, s) for t, _, s in market.calls] == [
        (0.0, "AAPL"),
        (3.0, "AAPL"),
        (6.0, "AAPL"),
        (9.0, "MSFT"),
    ]


@pytest.mark.parametrize(
    ("error", "reason"),
    [(RateLimited("429"), "rate_limited"), (ProviderUnavailable("boom"), "unavailable")],
)
def test_a_symbol_that_keeps_failing_gives_up_after_two_retries(market, clock, error, reason):
    market.add("AAPL")
    market.fail("get_quote", "AAPL", error=error)
    assert fetch(market, clock, ["AAPL"])["AAPL"] == Price("AAPL", None, reason)
    assert len(market.calls) == 3


def test_not_permitted_is_unpriced_at_once_without_a_retry(market, clock):
    market.add("AAPL")
    market.add("MSFT")
    market.fail("get_quote", "AAPL", error=NotPermitted("403"))
    got = fetch(market, clock, ["AAPL", "MSFT"])
    assert got["AAPL"] == Price("AAPL", None, "not_permitted")
    assert got["MSFT"].close is not None
    assert market.calls_for("AAPL") == ["get_quote"]


def test_a_rejected_key_propagates(market, clock):
    market.add("AAPL")
    market.fail("get_quote", error=KeyRejected("401"))
    with pytest.raises(KeyRejected):
        fetch(market, clock, ["AAPL"])


@pytest.mark.parametrize("symbol", ["aapl", "1ABC", "", "A B", "AAAAAAAAAAA", "A/B", "AAPL\n"])
def test_a_malformed_symbol_is_never_requested(market, clock, symbol):
    market.add("AAPL")
    got = fetch(market, clock, [symbol, "AAPL"])
    assert got[symbol] == Price(symbol, None, "malformed")
    assert market.calls_for(symbol) == []
    assert got["AAPL"].close is not None


@pytest.mark.parametrize("symbol", ["A", "BRK.B", "BF-B", "ABCDEFGHIJ"])
def test_well_formed_symbols_match_the_pattern(symbol):
    assert SYMBOL_PATTERN.fullmatch(symbol)


def test_symbols_not_reached_by_the_deadline_are_unpriced(market, clock):
    cfg = JournalConfig(5, 20, 30, 5)  # 3 s apart, 30 s deadline
    symbols = [f"S{i:02d}" for i in range(15)]
    for symbol in symbols:
        market.add(symbol)
    got = fetch(market, clock, symbols, cfg=cfg)
    priced = [s for s in symbols if got[s].close is not None]
    assert priced == symbols[:10]  # calls at t = 0, 3, ... 27
    assert all(got[s] == Price(s, None, "deadline") for s in symbols[10:])
    assert len(market.calls) == 10


def test_a_retry_that_would_start_after_the_deadline_gives_up(clock):
    cfg = JournalConfig(5, 20, 30, 5)
    market = Flaky("S00", RateLimited("429"), 99, clock=clock.monotonic, quote_time=at(19, 59))
    for symbol in ("S00", "S01"):
        market.add(symbol)
    got = fetch(market, clock, ["S00", "S01"], cfg=cfg)
    assert got["S00"].reason == "rate_limited"
    assert got["S01"].close is not None
