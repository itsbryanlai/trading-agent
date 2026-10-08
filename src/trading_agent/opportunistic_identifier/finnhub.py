"""The Opportunistic Identifier's Finnhub adapter (specs/011-opportunistic-identifier
research O2; contracts/ports.md).

Built like reference/finnhub.py, and copied rather than imported from it: the same
pattern, but the agent needs more fields than feature 004's port carries. Standard
library only. The key travels in the X-Finnhub-Token header, never in a URL, so it
can't leak through a logged URL or an exception message. Redirects are refused. No
retries here: pacing, backoff and the deadline are the service's job. Numbers become
Decimal at this boundary; anything unusable (zero, a boolean, a non-finite value, the
wrong type) becomes None, never zero.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from trading_agent.no_redirect import open_without_redirects
from trading_agent.opportunistic_identifier.ports import (
    CompanyProfile,
    Fundamentals,
    KeyRejected,
    Listing,
    NotPermitted,
    ProviderUnavailable,
    Quote,
    RateLimited,
)

BASE_URL = "https://finnhub.io/api/v1"
# The exchanges the Risk Gate lets a buy through (risk/rules.py US_LISTED_MICS), sorted. One
# request each: Finnhub redirects the all-US request (exchange=US alone) to its home page. A
# test keeps this equal to the gate's list.
SYMBOL_LIST_MICS = ("XASE", "XNAS", "XNYS")
TIMEOUT_SECONDS = 10  # the per-call socket timeout the run budget counts (config.py)
TEXT_MAX_CHARS = 100  # company name and industry are untrusted text sent to the model

# Finnhub's `/stock/metric` key -> the Fundamentals field it fills.
_METRIC_FIELDS = {
    "10DayAverageTradingVolume": "avg_volume_10d_millions",
    "52WeekHigh": "high_52w",
    "52WeekLow": "low_52w",
    "peTTM": "pe_ttm",
    "pbQuarterly": "pb",
    "psTTM": "ps_ttm",
    "grossMarginTTM": "gross_margin_ttm",
    "operatingMarginTTM": "operating_margin_ttm",
    "netProfitMarginTTM": "net_margin_ttm",
    "roeTTM": "roe_ttm",
    "revenueGrowthTTMYoy": "revenue_growth_ttm_yoy",
    "epsGrowthTTMYoy": "eps_growth_ttm_yoy",
    "totalDebt/totalEquityQuarterly": "debt_to_equity",
    "currentDividendYieldTTM": "dividend_yield",
    "beta": "beta",
}


class OIFinnhub:
    def __init__(
        self, api_key: str, *, opener: Callable | None = None, timeout: float = TIMEOUT_SECONDS
    ) -> None:
        self._key = api_key
        self._open = opener or open_without_redirects()  # a key never follows a redirect
        self._timeout = timeout

    def __repr__(self) -> str:
        return "OIFinnhub(<key hidden>)"

    __str__ = __repr__

    # --- the port ----------------------------------------------------------------

    def us_listings(self) -> dict[str, Listing]:
        """One request per exchange, merged. One failure fails the whole list: a partial
        list would make real symbols `not_listed`."""
        listings: dict[str, Listing] = {}
        for mic in SYMBOL_LIST_MICS:
            body = self._get("/stock/symbol", {"exchange": "US", "mic": mic}, per_symbol=False)
            if not isinstance(body, list):
                raise ProviderUnavailable("/stock/symbol: unexpected response shape")
            for item in body:
                if isinstance(item, dict) and isinstance(item.get("symbol"), str):
                    listings[item["symbol"]] = _merge(listings.get(item["symbol"]), item)
        return listings

    def quote(self, symbol: str) -> Quote:
        body = self._object("/quote", {"symbol": symbol})
        return Quote(
            symbol, _decimal(body.get("c")), _decimal(body.get("pc")), _timestamp(body.get("t"))
        )

    def profile(self, symbol: str) -> CompanyProfile:
        body = self._object("/stock/profile2", {"symbol": symbol})
        return CompanyProfile(
            symbol,
            _decimal(body.get("marketCapitalization")),
            _text(body.get("currency")),
            _text(body.get("finnhubIndustry"), TEXT_MAX_CHARS),
        )

    def fundamentals(self, symbol: str) -> Fundamentals:
        body = self._object("/stock/metric", {"symbol": symbol, "metric": "all"})
        metric = body.get("metric")
        if not isinstance(metric, dict):
            metric = {}
        return Fundamentals(
            symbol,
            **{field: _decimal(metric.get(key)) for key, field in _METRIC_FIELDS.items()},
            received_keys=tuple(sorted(key for key in metric if isinstance(key, str))),
        )

    # --- HTTP --------------------------------------------------------------------

    def _object(self, path: str, params: dict) -> dict:
        body = self._get(path, params, per_symbol=True)
        if not isinstance(body, dict):
            raise ProviderUnavailable(f"{path}: unexpected response shape")
        return body

    def _get(self, path: str, params: dict, *, per_symbol: bool):
        request = Request(
            f"{BASE_URL}{path}?{urlencode(params)}",
            headers={"X-Finnhub-Token": self._key, "Accept": "application/json"},
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            code = exc.code
            # 401 always means the key; 403 on one symbol's request means that symbol
            # isn't covered by the plan.
            if code == 401 or (code == 403 and not per_symbol):
                raise KeyRejected(f"{path}: HTTP {code}") from None
            if code == 403:
                raise NotPermitted(f"{path}: HTTP 403") from None
            if code == 429:
                raise RateLimited(f"{path}: HTTP 429") from None
            raise ProviderUnavailable(f"{path}: HTTP {code}") from None
        # URLError, timeouts, resets; HTTPException covers truncated or malformed
        # responses, which are neither OSError nor ValueError.
        except (OSError, ValueError, HTTPException) as exc:
            raise ProviderUnavailable(f"{path}: {type(exc).__name__}") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ProviderUnavailable(f"{path}: response is not JSON") from None


def _merge(seen: Listing | None, item: dict) -> Listing:
    listing = Listing(
        item["symbol"],
        _text(item.get("type")),
        _text(item.get("mic")),
        _text(item.get("description"), TEXT_MAX_CHARS),
    )
    if seen is not None and (seen.type, seen.mic) != (listing.type, listing.mic):
        # Which entry is right is unknowable: fail closed.
        return Listing(listing.symbol, None, None, None, conflicting=True)
    return listing


def _text(value, max_chars: int | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    return value if max_chars is None else value[:max_chars]


def _timestamp(value) -> datetime | None:
    """Finnhub's `t`: Unix seconds. Anything else, or 0, is unknown."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _decimal(value) -> Decimal | None:
    """A finite, non-zero number, or None. The provider uses 0 for "no data"."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number != 0 else None
