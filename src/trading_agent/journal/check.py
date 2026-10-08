"""`--check SYMBOL ...`: what the provider sends for a few names (contracts/journal-interface.md).

Fetches each symbol's quote, paced like a run, and prints one JSON line per symbol: `c`, `t`,
the session's open and close, and whether research J2 accepts the quote, with the reason when
not. It needs only the market-data key, writes nothing, and prints no key. The session is
today's, or the last one on a day the market is closed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime

from trading_agent.journal.config import JournalConfig
from trading_agent.journal.prices import SYMBOL_PATTERN, judge, session_window
from trading_agent.reference.provider import (
    KeyRejected,
    MarketDataProvider,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.journal")

MAX_SYMBOLS = 20


def valid_symbols(symbols: list[str]) -> bool:
    return 1 <= len(symbols) <= MAX_SYMBOLS and all(
        SYMBOL_PATTERN.fullmatch(s) is not None for s in symbols
    )


def run_check(
    symbols: list[str],
    market: MarketDataProvider,
    cfg: JournalConfig,
    *,
    now: datetime,
    sleep: Callable[[float], None],
    out: Callable[[str], None],
) -> int:
    """Print one line per symbol. Returns 0, or 1 when the provider rejects the key."""
    today = calendar.trading_day(now)
    day = today if calendar.is_session(today) else calendar.previous_session(today)
    window = session_window(day, cfg)
    session = {
        "session_open": calendar.open_time(day).isoformat(),
        "session_close": calendar.close_time(day).isoformat(),
    }
    pace = 60 / cfg.finnhub_calls_per_minute
    for index, symbol in enumerate(symbols):
        if index:
            sleep(pace)
        line: dict = {"symbol": symbol, "c": None, "t": None, **session}
        try:
            quote = market.get_quote(symbol)
        except KeyRejected:
            log.error("journal: failed: market_data_key_rejected")
            return 1
        except (NotPermitted, RateLimited, ProviderUnavailable) as exc:
            reason = _ERRORS[type(exc)]
            out(json.dumps({"check": {**line, "accepted": False, "reason": reason}}))
            continue
        price = judge(symbol, quote, window)
        line["c"] = None if quote.current is None else str(quote.current)
        line["t"] = None if quote.timestamp is None else quote.timestamp.isoformat()
        out(
            json.dumps(
                {"check": {**line, "accepted": price.close is not None, "reason": price.reason}}
            )
        )
    return 0


_ERRORS = {
    NotPermitted: "not_permitted",
    RateLimited: "rate_limited",
    ProviderUnavailable: "unavailable",
}
