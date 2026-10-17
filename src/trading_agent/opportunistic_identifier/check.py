"""`--check SYMBOL...`: what the provider sends for a few names (contracts/oi-interface.md;
quickstart step 2).

Fetches the named symbols, paced like a run, and prints per symbol the raw metric names the
provider sent, the values the agent reads from them, the derived values, and `eligible` or
the skip reason. No model call and no database. It prints no header and no key.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from trading_agent.opportunistic_identifier import screen
from trading_agent.opportunistic_identifier.config import OIConfig
from trading_agent.opportunistic_identifier.fetch import Fetcher
from trading_agent.opportunistic_identifier.outcome import MARKET_DATA_UNAVAILABLE, Failed
from trading_agent.opportunistic_identifier.ports import KeyRejected, MarketData, ProviderError
from trading_agent.opportunistic_identifier.prompt import number
from trading_agent.reference.normalize import ReferenceRow, normalize
from trading_agent.reference.symbols import is_plausible_ticker

log = logging.getLogger("trading_agent.opportunistic_identifier")

MAX_SYMBOLS = 10
_HUNDRED = 100
_CENT = Decimal("0.01")


def valid_symbols(symbols: list[str]) -> bool:
    return 1 <= len(symbols) <= MAX_SYMBOLS and all(is_plausible_ticker(s) for s in symbols)


def run_check(
    symbols: list[str],
    market: MarketData,
    cfg: OIConfig,
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    out: Callable[[str], None],
) -> int:
    """Print one line per symbol. Returns 0, or 1 when the provider rejects the key."""
    fetcher = Fetcher(
        market, cfg, clock=clock, sleep=sleep, monotonic=monotonic, deadline=float("inf")
    )
    try:
        listings = fetcher.listings()
        for symbol in symbols:
            out(json.dumps({"check": _symbol(symbol, listings.get(symbol), fetcher, cfg, clock)}))
    except KeyRejected as exc:
        log.error("opportunistic_identifier: %s: %s", MARKET_DATA_UNAVAILABLE, type(exc).__name__)
        return 1
    except Failed:  # the symbol list could not be fetched; the fetcher has logged why
        return 1
    return 0


def _symbol(symbol, listing, fetcher: Fetcher, cfg: OIConfig, clock) -> dict:
    line: dict = {"symbol": symbol}
    stop = screen.listing_stop(symbol, listing)
    if stop is not None:  # costs no call, as in a run
        return {**line, "result": stop.reason}
    line["listing"] = {"type": listing.type, "mic": listing.mic}
    max_age = fetcher.max_age
    try:
        quote = fetcher.paced(lambda: fetcher.market.quote(symbol))
        now = clock()
        line["quote"] = {
            "c": number(quote.current),
            "pc": number(quote.previous_close),
            "t": quote.timestamp.isoformat() if quote.timestamp else None,
        }
        if screen.is_stale(quote, now, max_age):
            return {**line, "result": screen.STALE_QUOTE}
        profile = fetcher.paced(lambda: fetcher.market.profile(symbol))
        fundamentals = fetcher.paced(lambda: fetcher.market.fundamentals(symbol))
    except KeyRejected:
        raise
    except ProviderError as exc:
        return {**line, "result": fetcher.reason(exc)}
    line["profile"] = {
        "market_cap_millions": number(profile.market_cap_millions),
        "currency": profile.currency,
    }
    values = asdict(fundamentals)
    line["metric_keys_received"] = list(fundamentals.received_keys)
    line["fundamentals"] = {
        key: number(value)
        for key, value in values.items()
        if key not in ("symbol", "received_keys")
    }
    line["derived"] = _derived(symbol, listing, profile, quote, fundamentals, now)
    result = screen.assess(
        symbol, listing, profile, quote, fundamentals, now, cfg.universe, max_age
    )
    return {**line, "result": "eligible" if isinstance(result, screen.Candidate) else result.reason}


def _derived(symbol, listing, profile, quote, fundamentals, now) -> dict:
    derived: dict = {}
    row = normalize(
        symbol,
        listing.to_reference(),
        profile.to_reference(),
        quote,
        fundamentals.to_metrics(),
        now,
    )
    if isinstance(row, ReferenceRow):
        derived.update(
            market_cap_usd=number(row.market_cap_usd),
            avg_daily_dollar_volume_usd=number(row.avg_daily_dollar_volume_usd),
            share_price_usd=number(row.share_price_usd),
        )
    price, previous, high = quote.current, quote.previous_close, fundamentals.high_52w
    if price and previous:
        derived["move_today_pct"] = number(
            ((price - previous) / previous * _HUNDRED).quantize(_CENT)
        )
    if price and high:
        derived["below_52w_high_pct"] = number(((high - price) / high * _HUNDRED).quantize(_CENT))
    return derived
