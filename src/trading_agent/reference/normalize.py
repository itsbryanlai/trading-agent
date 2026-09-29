"""Provider values in, one stored row or one failure out (research D2-D5). Pure.

Fail closed throughout: a missing row costs a skipped buy, a wrong row costs
exposure. Unknown security types become `other` and unmapped exchange codes are
kept as sent, so the gate's listing check rejects them. The provider uses 0 for
"no data", so zero is treated as missing. Values are rounded down, so rounding
can never lift one over a gate floor (adversarial review).

`now` is an argument: it decides which of the quote's prices is the previous
session's close (research D2, review H1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, DecimalException

from trading_agent.reference.provider import Listing, Metrics, Profile, Quote
from trading_agent.risk import calendar

# Failure reasons: exactly contracts/reference-data-interface.md "Failure reasons".
INVALID_SYMBOL = "invalid_symbol"
NOT_LISTED = "not_listed"
CONFLICTING_LISTING = "conflicting_listing"
MISSING_TYPE = "missing_type"
MISSING_MIC = "missing_mic"
NON_USD_MARKET_CAP = "non_usd_market_cap"
MISSING_MARKET_CAP = "missing_market_cap"
STALE_QUOTE = "stale_quote"
MISSING_PRICE = "missing_price"
MISSING_VOLUME = "missing_volume"
IMPLAUSIBLE_MARKET_CAP = "implausible_market_cap"
IMPLAUSIBLE_DOLLAR_VOLUME = "implausible_dollar_volume"
VALUE_OUT_OF_RANGE = "value_out_of_range"
PROVIDER_UNAVAILABLE = "provider_unavailable"
NOT_PERMITTED = "not_permitted"
DATABASE_ERROR = "database_error"
RATE_LIMITED = "rate_limited"
INTERNAL_ERROR = "internal_error"
ALL_REASONS = (
    INVALID_SYMBOL,
    NOT_LISTED,
    CONFLICTING_LISTING,
    MISSING_TYPE,
    MISSING_MIC,
    NON_USD_MARKET_CAP,
    MISSING_MARKET_CAP,
    STALE_QUOTE,
    MISSING_PRICE,
    MISSING_VOLUME,
    IMPLAUSIBLE_MARKET_CAP,
    IMPLAUSIBLE_DOLLAR_VOLUME,
    VALUE_OUT_OF_RANGE,
    PROVIDER_UNAVAILABLE,
    NOT_PERMITTED,
    DATABASE_ERROR,
    RATE_LIMITED,
    INTERNAL_ERROR,
)

MILLION = Decimal(1_000_000)
# About four times the largest company: above it, the provider's unit is wrong (D5).
MAX_MARKET_CAP_USD = Decimal("20000000000000")
_CENT = Decimal("0.01")
_PRICE_SCALE = Decimal("0.0001")
# Exclusive upper bounds of numeric(20,2) and numeric(14,4) (migration 0006).
_MONEY_LIMIT = Decimal(10) ** 18
_PRICE_LIMIT = Decimal(10) ** 10

_TYPES = {"common stock": "common_stock", "etp": "etf", "etf": "etf", "adr": "adr"}
_NASDAQ_TIERS = {"XNGS", "XNMS", "XNCM", "XNAS"}


@dataclass(frozen=True)
class ReferenceRow:
    symbol: str
    security_type: str
    exchange_mic: str
    market_cap_usd: Decimal
    avg_daily_dollar_volume_usd: Decimal
    share_price_usd: Decimal


@dataclass(frozen=True)
class Failure:
    symbol: str
    reason: str


def security_type(provider_type: str) -> str:
    return _TYPES.get(provider_type.strip().casefold(), "other")


def exchange_mic(provider_mic: str) -> str:
    mic = provider_mic.strip().upper()
    return "XNAS" if mic in _NASDAQ_TIERS else mic


def _positive(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value > 0


def listing_failure(symbol: str, listing: Listing | None) -> Failure | None:
    """The checks that need only the symbol list, run before any per-symbol call
    so a made-up ticker costs nothing (review M2)."""
    if listing is None:
        return Failure(symbol, NOT_LISTED)
    if listing.conflicting:
        return Failure(symbol, CONFLICTING_LISTING)
    if not (listing.type or "").strip():
        return Failure(symbol, MISSING_TYPE)
    if not (listing.mic or "").strip():
        return Failure(symbol, MISSING_MIC)
    return None


def previous_close(quote: Quote, now: datetime) -> Decimal | str | None:
    """The previous session's close, or STALE_QUOTE (research D2, review H1).

    The quote's time `t` says which session its prices belong to. If `t` is on
    today's trading day (pre-market or in session), the quote has rolled over and
    `pc` is the previous session's close. If `t` is on the previous session's day,
    it hasn't rolled yet and `c` is that session's last price. Anything older
    (a halted or delisted symbol) is stale: fail closed.
    """
    when = quote.timestamp
    if when is None:
        return STALE_QUOTE
    today = calendar.trading_day(now)
    day = calendar.trading_day(when)
    if day == today:
        return quote.previous_close
    if day == calendar.previous_session(today):
        return quote.current
    return STALE_QUOTE


def normalize(
    symbol: str,
    listing: Listing | None,
    profile: Profile,
    quote: Quote,
    metrics: Metrics,
    now: datetime,
) -> ReferenceRow | Failure:
    failure = listing_failure(symbol, listing)
    if failure is not None:
        return failure
    if (profile.currency or "").strip().upper() != "USD":
        # Market cap in another currency would be stored as dollars (review H2).
        return Failure(symbol, NON_USD_MARKET_CAP)
    cap_millions = profile.market_cap_millions
    if not _positive(cap_millions):
        return Failure(symbol, MISSING_MARKET_CAP)
    price = previous_close(quote, now)
    if price == STALE_QUOTE:
        return Failure(symbol, STALE_QUOTE)
    if not _positive(price):
        return Failure(symbol, MISSING_PRICE)
    volume_millions = metrics.avg_volume_10d_millions
    if not _positive(volume_millions):
        return Failure(symbol, MISSING_VOLUME)

    try:
        return _row(symbol, listing, cap_millions, price, volume_millions)
    except DecimalException:
        # Extreme exponents can overflow or make quantize invalid (review M3).
        return Failure(symbol, VALUE_OUT_OF_RANGE)


def _row(symbol, listing, cap_millions, price, volume_millions) -> ReferenceRow | Failure:
    market_cap = cap_millions * MILLION
    if market_cap > MAX_MARKET_CAP_USD:
        return Failure(symbol, IMPLAUSIBLE_MARKET_CAP)
    dollar_volume = volume_millions * MILLION * price
    if dollar_volume > market_cap:
        return Failure(symbol, IMPLAUSIBLE_DOLLAR_VOLUME)

    # Down, never up: rounding must not lift a value over a gate floor.
    market_cap = market_cap.quantize(_CENT, rounding=ROUND_DOWN)
    dollar_volume = dollar_volume.quantize(_CENT, rounding=ROUND_DOWN)
    price = price.quantize(_PRICE_SCALE, rounding=ROUND_DOWN)
    if not (
        0 < market_cap < _MONEY_LIMIT
        and 0 < dollar_volume < _MONEY_LIMIT
        and 0 < price < _PRICE_LIMIT
    ):
        return Failure(symbol, VALUE_OUT_OF_RANGE)

    return ReferenceRow(
        symbol=symbol,
        security_type=security_type(listing.type),
        exchange_mic=exchange_mic(listing.mic),
        market_cap_usd=market_cap,
        avg_daily_dollar_volume_usd=dollar_volume,
        share_price_usd=price,
    )
