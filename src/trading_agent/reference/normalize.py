"""Provider values in, one stored row or one failure out (research D2-D5). Pure.

Fail closed throughout: a missing row costs a skipped buy, a wrong row costs
exposure. Unknown security types become `other` and unmapped exchange codes are
kept as sent, so the gate's listing check rejects them. The provider uses 0 for
"no data", so zero is treated as missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from trading_agent.reference.provider import Listing, Metrics, Profile, Quote

# Failure reasons: exactly contracts/reference-data-interface.md "Failure reasons".
INVALID_SYMBOL = "invalid_symbol"
NOT_LISTED = "not_listed"
MISSING_TYPE = "missing_type"
MISSING_MIC = "missing_mic"
MISSING_MARKET_CAP = "missing_market_cap"
MISSING_PRICE = "missing_price"
MISSING_VOLUME = "missing_volume"
IMPLAUSIBLE_MARKET_CAP = "implausible_market_cap"
IMPLAUSIBLE_DOLLAR_VOLUME = "implausible_dollar_volume"
VALUE_OUT_OF_RANGE = "value_out_of_range"
PROVIDER_UNAVAILABLE = "provider_unavailable"
DATABASE_ERROR = "database_error"
RATE_LIMITED = "rate_limited"
ALL_REASONS = (
    INVALID_SYMBOL,
    NOT_LISTED,
    MISSING_TYPE,
    MISSING_MIC,
    MISSING_MARKET_CAP,
    MISSING_PRICE,
    MISSING_VOLUME,
    IMPLAUSIBLE_MARKET_CAP,
    IMPLAUSIBLE_DOLLAR_VOLUME,
    VALUE_OUT_OF_RANGE,
    PROVIDER_UNAVAILABLE,
    DATABASE_ERROR,
    RATE_LIMITED,
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


def normalize(
    symbol: str,
    listing: Listing | None,
    profile: Profile,
    quote: Quote,
    metrics: Metrics,
) -> ReferenceRow | Failure:
    if listing is None:
        return Failure(symbol, NOT_LISTED)
    if not (listing.type or "").strip():
        return Failure(symbol, MISSING_TYPE)
    if not (listing.mic or "").strip():
        return Failure(symbol, MISSING_MIC)
    cap_millions = profile.market_cap_millions
    if not _positive(cap_millions):
        return Failure(symbol, MISSING_MARKET_CAP)
    price = quote.previous_close
    if not _positive(price):
        return Failure(symbol, MISSING_PRICE)
    volume_millions = metrics.avg_volume_10d_millions
    if not _positive(volume_millions):
        return Failure(symbol, MISSING_VOLUME)

    market_cap = cap_millions * MILLION
    if market_cap > MAX_MARKET_CAP_USD:
        return Failure(symbol, IMPLAUSIBLE_MARKET_CAP)
    dollar_volume = volume_millions * MILLION * price
    if dollar_volume > market_cap:
        return Failure(symbol, IMPLAUSIBLE_DOLLAR_VOLUME)

    market_cap = market_cap.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    dollar_volume = dollar_volume.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    price = price.quantize(_PRICE_SCALE, rounding=ROUND_HALF_EVEN)
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
