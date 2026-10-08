"""Fetch each symbol's closing price (research J2, J8).

One `/quote` per symbol, in symbol order, paced at `finnhub_calls_per_minute`. A quote is
today's close only when its price is usable and its own trade time falls inside today's
session plus a short grace for the closing auction; anything else is unpriced for the day.
`RateLimited` and `ProviderUnavailable` are retried at most twice, one pacing interval apart;
`NotPermitted` is unpriced at once; `KeyRejected` propagates and fails the run. Symbols not
reached before the deadline are unpriced as `deadline`. Time is injected, never read here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from trading_agent.journal.config import JournalConfig
from trading_agent.journal.model import Price
from trading_agent.reference.provider import (
    MarketDataProvider,
    NotPermitted,
    ProviderUnavailable,
    Quote,
    RateLimited,
)
from trading_agent.risk import calendar

SYMBOL_PATTERN = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
RETRIES = 2


def fetch_closes(
    provider: MarketDataProvider,
    symbols: list[str] | tuple[str, ...] | set[str],
    day: date,
    cfg: JournalConfig,
    *,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> dict[str, Price]:
    """`symbol -> Price` for every distinct symbol asked about, in symbol order."""
    window = session_window(day, cfg)
    pace = 60 / cfg.finnhub_calls_per_minute
    started = monotonic()
    fetcher = _Fetcher(
        provider, window, pace, started + cfg.fetch_deadline_seconds, sleep, monotonic
    )
    return {symbol: fetcher.price(symbol) for symbol in sorted(set(symbols))}


def session_window(day: date, cfg: JournalConfig) -> _Window:
    """Today's session plus the grace for the closing auction (research J2)."""
    return _Window(
        calendar.open_time(day),
        calendar.close_time(day) + timedelta(minutes=cfg.close_grace_minutes),
    )


def judge(symbol: str, quote: Quote, window: _Window) -> Price:
    """Whether a quote is today's close: a usable price, stamped inside the window. Stamped
    missing or before the open is `stale` (permanent); after the grace is `after_close`
    (systemic: the provider reports after-hours prices)."""
    current: Decimal | None = quote.current
    if current is None or current <= 0:
        return Price(symbol, None, "no_price")
    stamp = quote.timestamp
    if stamp is None or stamp < window.start:
        return Price(symbol, None, "stale")
    if stamp > window.end:
        return Price(symbol, None, "after_close")
    return Price(symbol, current, None)


class _Window:
    def __init__(self, start, end) -> None:
        self.start, self.end = start, end


class _Fetcher:
    def __init__(self, provider, window, pace, deadline, sleep, monotonic) -> None:
        self._provider = provider
        self._window = window
        self._pace = pace
        self._deadline = deadline
        self._sleep = sleep
        self._monotonic = monotonic
        self._called = False

    def price(self, symbol: str) -> Price:
        if SYMBOL_PATTERN.fullmatch(symbol) is None:
            return Price(symbol, None, "malformed")
        reason = "unavailable"
        for _ in range(RETRIES + 1):
            if self._called:
                self._sleep(self._pace)
            if self._monotonic() >= self._deadline:
                return Price(symbol, None, "deadline")
            self._called = True
            try:
                quote = self._provider.get_quote(symbol)
            except NotPermitted:
                return Price(symbol, None, "not_permitted")
            except RateLimited:
                reason = "rate_limited"
                continue
            except ProviderUnavailable:
                reason = "unavailable"
                continue
            return self._judge(symbol, quote)
        return Price(symbol, None, reason)

    def _judge(self, symbol: str, quote: Quote) -> Price:
        return judge(symbol, quote, self._window)
