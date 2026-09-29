"""The Finnhub adapter (research D2, D6; contracts/market-data-port.md).

Standard library only. The key travels in the X-Finnhub-Token header, never in
a URL, so it can't leak through a logged URL or an exception message. No retries
here: retrying and pacing are the service's job. Numbers become Decimal at this
boundary; anything unusable becomes None, never zero.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from trading_agent.reference.provider import (
    KeyRejected,
    Listing,
    Metrics,
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
        body = self._get("/stock/symbol", {"exchange": "US"})
        if not isinstance(body, list):
            raise ProviderUnavailable("/stock/symbol: unexpected response shape")
        listings: dict[str, Listing] = {}
        for item in body:
            if isinstance(item, dict) and isinstance(item.get("symbol"), str):
                symbol = item["symbol"]
                listings[symbol] = Listing(symbol, _text(item.get("type")), _text(item.get("mic")))
        return listings

    def get_profile(self, symbol: str) -> Profile:
        body = self._object("/stock/profile2", {"symbol": symbol})
        return Profile(symbol, _decimal(body.get("marketCapitalization")))

    def get_quote(self, symbol: str) -> Quote:
        body = self._object("/quote", {"symbol": symbol})
        return Quote(symbol, _decimal(body.get("pc")))

    def get_metrics(self, symbol: str) -> Metrics:
        body = self._object("/stock/metric", {"symbol": symbol, "metric": "all"})
        metric = body.get("metric")
        volume = metric.get("10DayAverageTradingVolume") if isinstance(metric, dict) else None
        return Metrics(symbol, _decimal(volume))

    # --- HTTP --------------------------------------------------------------------

    def _object(self, path: str, params: dict) -> dict:
        body = self._get(path, params)
        if not isinstance(body, dict):
            raise ProviderUnavailable(f"{path}: unexpected response shape")
        return body

    def _get(self, path: str, params: dict):
        request = Request(
            f"{BASE_URL}{path}?{urlencode(params)}",
            headers={"X-Finnhub-Token": self._key, "Accept": "application/json"},
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            code = exc.code
            if code in (401, 403):
                raise KeyRejected(f"{path}: HTTP {code}") from None
            if code == 429:
                raise RateLimited(f"{path}: HTTP 429") from None
            raise ProviderUnavailable(f"{path}: HTTP {code}") from None
        except (OSError, ValueError) as exc:  # URLError, timeouts, resets
            raise ProviderUnavailable(f"{path}: {type(exc).__name__}") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ProviderUnavailable(f"{path}: response is not JSON") from None


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _decimal(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None
