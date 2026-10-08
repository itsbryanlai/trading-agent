"""Which fetched names are worth the model's attention (specs/011 research O4, O5, O6). Pure.

`assess` turns one fetched name into a `Candidate` or a `Skip` with a reason from the
contract's closed set. `shortlist` leaves out names with an open report, ranks the rest
twice (today's move and the distance below the 52-week high, largest fall first, as 1-based
ordinals with equal values ordered by symbol), averages the two ranks, and keeps the lowest
scores. Nothing here reads the clock: `now` is an argument.

The universe rule is the Risk Gate's, so the agent doesn't flag a name the gate would reject
on the same data. Values come from `reference.normalize.normalize`, the reference-data job's
own derivation.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from trading_agent.opportunistic_identifier import text
from trading_agent.opportunistic_identifier.ports import (
    CompanyProfile,
    Fundamentals,
    Listing,
    Quote,
)
from trading_agent.reference.normalize import (
    Failure,
    ReferenceRow,
    exchange_mic,
    listing_failure,
    normalize,
    security_type,
)
from trading_agent.risk import calendar, rules

STALE_QUOTE = "stale_quote"
MISSING_PRICE = "missing_price"
MISSING_52_WEEK_HIGH = "missing_52_week_high"
MISSING_FUNDAMENTALS = "missing_fundamentals"
INCONSISTENT_52_WEEK_RANGE = "inconsistent_52_week_range"
IMPLAUSIBLE_MOVE = "implausible_move"

# The price may sit this far outside the provider's 52-week range: beyond it the range is
# likelier unadjusted for a split than the price a record (review M1).
RANGE_SLACK_ABOVE = Decimal("1.1")
RANGE_SLACK_BELOW = Decimal("0.9")
MAX_MOVE = Decimal("0.5")  # beyond this a split or a bad print is likelier than a price
NAME_MAX_CHARS = 100


class UniverseFloors(Protocol):
    """The universe floors of `risk.config.UniverseConfig`, by shape: only the OI's config
    loader reads the risk file, so this module doesn't import its loader module."""

    min_market_cap_usd: Decimal
    min_avg_daily_dollar_volume_usd: Decimal
    min_share_price_usd: Decimal


@dataclass(frozen=True)
class Skip:
    symbol: str
    reason: str


@dataclass(frozen=True)
class NameData:
    """Everything fetched for one name in one run. `name` and `industry` are the
    provider's untrusted text, cleaned and cut."""

    symbol: str
    listing: Listing
    profile: CompanyProfile
    quote: Quote
    fundamentals: Fundamentals
    fetched_at: datetime
    name: str | None
    industry: str | None


@dataclass(frozen=True)
class Candidate:
    symbol: str
    move_today: Decimal  # (c - pc) / pc
    below_high: Decimal  # (52WeekHigh - c) / 52WeekHigh; negative above the stored high
    rank_move: int = 0
    rank_high: int = 0
    score: Decimal = Decimal(0)
    data: NameData | None = None
    row: ReferenceRow | None = None


@dataclass(frozen=True)
class Shortlist:
    candidates: tuple[Candidate, ...]
    already_open: int
    eligible: int  # candidates left after the open names are out


def listing_stop(symbol: str, listing: Listing | None) -> Skip | None:
    """The checks that need only the symbol list, run before any per-name call, so an ETF,
    an OTC name or a made-up ticker costs nothing (research O5 step 1)."""
    failure = listing_failure(symbol, None if listing is None else listing.to_reference())
    if failure is not None:
        return Skip(symbol, failure.reason)
    if (
        security_type(listing.type) != "common_stock"
        or exchange_mic(listing.mic) not in rules.US_LISTED_MICS
    ):
        return Skip(symbol, rules.UNIVERSE_LISTING)
    return None


def universe_stop(row: ReferenceRow, universe: UniverseFloors) -> str | None:
    """A copy of the gate's universe comparisons, with its rule names
    (`risk/gate.py:_universe_stop`; a property test keeps the two equal). The gate's own
    check is private and the gate is not touched by this feature."""
    if row.security_type != "common_stock" or row.exchange_mic not in rules.US_LISTED_MICS:
        return rules.UNIVERSE_LISTING
    if row.market_cap_usd < universe.min_market_cap_usd:
        return rules.UNIVERSE_MARKET_CAP
    if row.avg_daily_dollar_volume_usd < universe.min_avg_daily_dollar_volume_usd:
        return rules.UNIVERSE_DOLLAR_VOLUME
    if row.share_price_usd < universe.min_share_price_usd:
        return rules.UNIVERSE_SHARE_PRICE
    return None


def assess(
    symbol: str,
    listing: Listing | None,
    profile: CompanyProfile,
    quote: Quote,
    fundamentals: Fundamentals,
    now: datetime,
    universe: UniverseFloors,
    quote_max_age: timedelta,
) -> Candidate | Skip:
    stop = listing_stop(symbol, listing)
    if stop is not None:
        return stop
    if is_stale(quote, now, quote_max_age):
        return Skip(symbol, STALE_QUOTE)
    price, previous = quote.current, quote.previous_close
    if not _positive(price) or not _positive(previous):
        return Skip(symbol, MISSING_PRICE)

    row = normalize(
        symbol,
        listing.to_reference(),
        profile.to_reference(),
        quote,
        fundamentals.to_metrics(),
        now,
    )
    if isinstance(row, Failure):
        return Skip(symbol, row.reason)
    stop_reason = universe_stop(row, universe)
    if stop_reason is not None:
        return Skip(symbol, stop_reason)
    high = fundamentals.high_52w
    if not _positive(high):
        return Skip(symbol, MISSING_52_WEEK_HIGH)
    if _range_inconsistent(price, high, fundamentals.low_52w):
        return Skip(symbol, INCONSISTENT_52_WEEK_RANGE)
    if fundamentals.pe_ttm is None and fundamentals.pb is None:
        return Skip(symbol, MISSING_FUNDAMENTALS)
    move = (price - previous) / previous
    if abs(move) > MAX_MOVE:
        return Skip(symbol, IMPLAUSIBLE_MOVE)

    data = NameData(
        symbol=symbol,
        listing=listing,
        profile=profile,
        quote=quote,
        fundamentals=fundamentals,
        fetched_at=now,
        name=_clean(listing.description),
        industry=_clean(profile.industry),
    )
    return Candidate(symbol, move, (high - price) / high, data=data, row=row)


def shortlist(
    candidates: Sequence[Candidate], open_symbols: Collection[str], size: int
) -> Shortlist:
    remaining = [c for c in candidates if c.symbol not in open_symbols]
    by_move = sorted(remaining, key=lambda c: (c.move_today, c.symbol))
    by_high = sorted(remaining, key=lambda c: (-c.below_high, c.symbol))
    rank_move = {c.symbol: n for n, c in enumerate(by_move, 1)}
    rank_high = {c.symbol: n for n, c in enumerate(by_high, 1)}
    ranked = [
        replace(
            c,
            rank_move=rank_move[c.symbol],
            rank_high=rank_high[c.symbol],
            score=Decimal(rank_move[c.symbol] + rank_high[c.symbol]) / 2,
        )
        for c in remaining
    ]
    ranked.sort(key=lambda c: (c.score, c.symbol))
    return Shortlist(
        candidates=tuple(ranked[:size]),
        already_open=len(candidates) - len(remaining),
        eligible=len(remaining),
    )


def is_stale(quote: Quote, now: datetime, max_age: timedelta) -> bool:
    """Fresh only if the quote's own trade time is on today's session and within `max_age`."""
    when = quote.timestamp
    if when is None:
        return True
    return calendar.trading_day(when) != calendar.trading_day(now) or now - when > max_age


def _range_inconsistent(price: Decimal, high: Decimal, low: Decimal | None) -> bool:
    """A split the provider hasn't adjusted its 52-week high and low for makes a name look
    like it has collapsed. A low above the high, a price well above the high, or (when there
    is a low) a price well below it, says the range and the price aren't on the same scale."""
    if price > high * RANGE_SLACK_ABOVE:
        return True
    return low is not None and (low > high or price < low * RANGE_SLACK_BELOW)


def _positive(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value > 0


def _clean(value: str | None) -> str | None:
    return None if value is None else text.clean(value)[:NAME_MAX_CHARS]
