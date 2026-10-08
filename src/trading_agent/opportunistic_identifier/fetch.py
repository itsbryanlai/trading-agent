"""The fetch: the symbol list once, then each name's quote, profile and fundamentals (specs/011
research O2, O5, O10, O11).

Paced at `finnhub_calls_per_minute` (FR-021), with a deadline after which no call starts
(research O10). Names that fail the listing check cost no call, and a stale quote costs one
call, not three. Partial data never fails the run (FR-018); a rejected key, or every fetch
failing, does. The pacer is Research's pattern, copied: siblings never import each other.

A 429 backs the pace off for a minute before the next call, and the run goes on until the
deadline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from trading_agent.opportunistic_identifier import screen
from trading_agent.opportunistic_identifier.config import OIConfig
from trading_agent.opportunistic_identifier.outcome import (
    FETCH_FAILURES,
    MARKET_DATA_UNAVAILABLE,
    NOT_FETCHED,
    NOT_PERMITTED,
    PROVIDER_UNAVAILABLE,
    RATE_LIMITED,
    SYMBOL_LIST_UNAVAILABLE,
    Failed,
    RunOutcome,
)
from trading_agent.opportunistic_identifier.ports import (
    KeyRejected,
    Listing,
    MarketData,
    NotPermitted,
    ProviderError,
    RateLimited,
)
from trading_agent.opportunistic_identifier.rotation import ScanSlice
from trading_agent.opportunistic_identifier.screen import Skip

log = logging.getLogger("trading_agent.opportunistic_identifier")

RATE_LIMIT_BACKOFF_SECONDS = 60.0
SYMBOL_LIST_WEIGHT = 3  # the symbol list is one request per exchange


class _OutOfTime(Exception):
    """The fetch deadline passed: no further call starts."""


class Fetcher:
    def __init__(
        self,
        market: MarketData,
        cfg: OIConfig,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
        deadline: float,
    ) -> None:
        self.market, self.cfg = market, cfg
        self.clock, self.sleep, self.monotonic = clock, sleep, monotonic
        self._deadline = deadline
        self._interval = 60 / cfg.finnhub_calls_per_minute
        self.max_age = timedelta(minutes=cfg.quote_max_age_minutes)
        self._last_weight: int | None = None  # None until the first Finnhub call
        self._backoff = 0.0

    def fetch(self, scan: ScanSlice, outcome: RunOutcome) -> list[screen.Candidate]:
        listings = self.listings()
        candidates: list[screen.Candidate] = []
        attempted = failed = 0
        for symbol in scan.symbols:
            listing = listings.get(symbol)
            stop = screen.listing_stop(symbol, listing)
            if stop is None:
                attempted += 1
            result = stop or self._name(symbol, listing, outcome)
            if isinstance(result, screen.Candidate):
                candidates.append(result)
                continue
            outcome.skip(result)
            failed += stop is None and result.reason in FETCH_FAILURES
        if attempted and failed == attempted:
            log.error("opportunistic_identifier: %s: every fetch failed", MARKET_DATA_UNAVAILABLE)
            raise Failed(MARKET_DATA_UNAVAILABLE)
        return candidates

    def listings(self) -> dict[str, Listing]:
        try:
            return self.paced(self.market.us_listings, weight=SYMBOL_LIST_WEIGHT)
        except KeyRejected as exc:
            log.error(
                "opportunistic_identifier: %s: %s", MARKET_DATA_UNAVAILABLE, type(exc).__name__
            )
            raise Failed(MARKET_DATA_UNAVAILABLE) from None
        except (ProviderError, _OutOfTime) as exc:
            log.error(
                "opportunistic_identifier: %s: %s", SYMBOL_LIST_UNAVAILABLE, type(exc).__name__
            )
            raise Failed(SYMBOL_LIST_UNAVAILABLE) from None

    def _name(self, symbol: str, listing: Listing, outcome: RunOutcome) -> screen.Candidate | Skip:
        try:
            quote = self.paced(lambda: self.market.quote(symbol))
            outcome.counts.fetched += 1
            fetched_at = self.clock()
            if screen.is_stale(quote, fetched_at, self.max_age):
                return Skip(symbol, screen.STALE_QUOTE)  # a stale name costs one call, not three
            profile = self.paced(lambda: self.market.profile(symbol))
            fundamentals = self.paced(lambda: self.market.fundamentals(symbol))
        except _OutOfTime:
            return Skip(symbol, NOT_FETCHED)
        except KeyRejected as exc:
            log.error(
                "opportunistic_identifier: %s: %s", MARKET_DATA_UNAVAILABLE, type(exc).__name__
            )
            raise Failed(MARKET_DATA_UNAVAILABLE) from None
        except ProviderError as exc:
            return Skip(symbol, self.reason(exc))
        return screen.assess(
            symbol,
            listing,
            profile,
            quote,
            fundamentals,
            fetched_at,
            self.cfg.universe,
            self.max_age,
        )

    def reason(self, exc: ProviderError) -> str:
        if isinstance(exc, RateLimited):
            self._backoff = RATE_LIMIT_BACKOFF_SECONDS
            return RATE_LIMITED
        return NOT_PERMITTED if isinstance(exc, NotPermitted) else PROVIDER_UNAVAILABLE

    def paced(self, call, *, weight: int = 1):
        """Space Finnhub calls by the configured pace (the call after the symbol list waits
        for its three requests, and after a 429 for a minute). No call starts after the
        deadline, and none is waited for if it would start after it."""
        wait = 0.0 if self._last_weight is None else self._interval * self._last_weight
        wait = max(wait, self._backoff)
        if self.monotonic() + wait >= self._deadline:
            raise _OutOfTime
        if wait:
            self.sleep(wait)
        self._backoff = 0.0
        self._last_weight = weight
        return call()
