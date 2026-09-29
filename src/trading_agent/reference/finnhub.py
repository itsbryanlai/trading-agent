"""The Finnhub adapter (research D2, D6; contracts/market-data-port.md).

Standard library only. The key travels in the X-Finnhub-Token header, never in
a URL, so it can't leak through a logged URL or an exception message. No retries
here: retrying and pacing are the service's job. Numbers become Decimal at this
boundary; anything unusable becomes None, never zero.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from trading_agent.reference.provider import (
    KeyRejected,
    Listing,
    Metrics,
    NotPermitted,
    Profile,
    ProviderUnavailable,
    Quote,
    RateLimited,
)

BASE_URL = "https://finnhub.io/api/v1"
TIMEOUT_SECONDS = 10


class FinnhubProvider:
    def __init__(
        self,
        api_key: str,
        *,
        opener: Callable | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._key = api_key
        self._open = opener or urlopen
        self._timeout = timeout

    def __repr__(self) -> str:
        return "FinnhubProvider(<key hidden>)"

    __str__ = __repr__

    # --- the port ----------------------------------------------------------------

    def list_us_symbols(self) -> dict[str, Listing]:
        body = self._get("/stock/symbol", {"exchange": "US"}, per_symbol=False)
        if not isinstance(body, list):
            raise ProviderUnavailable("/stock/symbol: unexpected response shape")
        listings: dict[str, Listing] = {}
        for item in body:
            if isinstance(item, dict) and isinstance(item.get("symbol"), str):
                symbol = item["symbol"]
                listing = Listing(symbol, _text(item.get("type")), _text(item.get("mic")))
                seen = listings.get(symbol)
                if seen is not None and (seen.type, seen.mic) != (listing.type, listing.mic):
                    # Which entry is right is unknowable: fail closed (review LOW).
                    listing = Listing(symbol, None, None, conflicting=True)
                listings[symbol] = listing
        return listings

    def get_profile(self, symbol: str) -> Profile:
        body = self._object("/stock/profile2", {"symbol": symbol})
        return Profile(
            symbol, _decimal(body.get("marketCapitalization")), _text(body.get("currency"))
        )

    def get_quote(self, symbol: str) -> Quote:
        body = self._object("/quote", {"symbol": symbol})
        return Quote(
            symbol, _decimal(body.get("c")), _decimal(body.get("pc")), _timestamp(body.get("t"))
        )

    def get_metrics(self, symbol: str) -> Metrics:
        body = self._object("/stock/metric", {"symbol": symbol, "metric": "all"})
        metric = body.get("metric")
        volume = metric.get("10DayAverageTradingVolume") if isinstance(metric, dict) else None
        return Metrics(symbol, _decimal(volume))

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
            # 401 always means the key; 403 on one symbol's request means that
            # symbol isn't covered by the plan (review M1).
            if code == 401 or (code == 403 and not per_symbol):
                raise KeyRejected(f"{path}: HTTP {code}") from None
            if code == 403:
                raise NotPermitted(f"{path}: HTTP 403") from None
            if code == 429:
                raise RateLimited(f"{path}: HTTP 429") from None
            raise ProviderUnavailable(f"{path}: HTTP {code}") from None
        # URLError, timeouts, resets; HTTPException covers truncated or malformed
        # responses (IncompleteRead, BadStatusLine, LineTooLong), which are
        # neither OSError nor ValueError (converge T041).
        except (OSError, ValueError, HTTPException) as exc:
            raise ProviderUnavailable(f"{path}: {type(exc).__name__}") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ProviderUnavailable(f"{path}: response is not JSON") from None


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _timestamp(value) -> datetime | None:
    """Finnhub's `t`: Unix seconds. Anything else, or 0, is unknown."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _decimal(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None
