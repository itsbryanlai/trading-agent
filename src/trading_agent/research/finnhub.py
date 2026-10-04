"""Finnhub news for Research (specs/007-research-agent research R3; contracts/ports.md).

Standard library only, in the same style as reference/finnhub.py: the key travels in
the X-Finnhub-Token header, never in a URL; no retries here (pacing and the news
deadline are the service's job). An item that can't be cited safely (no http(s) URL,
no headline, no time) is skipped at this boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from trading_agent.no_redirect import open_without_redirects
from trading_agent.research import text
from trading_agent.research.ports import (
    KeyRejected,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
    RawArticle,
)

BASE_URL = "https://finnhub.io/api/v1"
# The exchanges the Risk Gate lets a buy through (risk/rules.py US_LISTED_MICS). Research may
# not import the gate's modules, so a test keeps this equal to it. One request each.
SYMBOL_LIST_MICS = ("XASE", "XNAS", "XNYS")
TIMEOUT_SECONDS = 10  # counted in the run budget (config.NEWS_CALL_TIMEOUT_SECONDS)


class FinnhubNews:
    def __init__(
        self, api_key: str, *, opener: Callable | None = None, timeout: float = TIMEOUT_SECONDS
    ) -> None:
        self._key = api_key
        self._open = opener or open_without_redirects()  # review L4
        self._timeout = timeout

    def __repr__(self) -> str:
        return "FinnhubNews(<key hidden>)"

    __str__ = __repr__

    # --- the port ----------------------------------------------------------------

    def general_news(self) -> list[RawArticle]:
        return _articles(self._get("/news", {"category": "general"}, per_symbol=False))

    def company_news(self, symbol: str, start: date, end: date) -> list[RawArticle]:
        params = {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat()}
        return _articles(self._get("/company-news", params, per_symbol=True))

    def us_symbols(self) -> dict[str, str]:
        """Symbol → company name (Finnhub's `description`), used by the relevance rule.

        One request per exchange the Risk Gate allows, merged: Finnhub redirects the
        all-US request (`exchange=US` alone) to its home page. One failure fails the
        whole list, since a partial list would call real symbols unlisted."""
        names: dict[str, str] = {}
        for mic in SYMBOL_LIST_MICS:
            body = self._get("/stock/symbol", {"exchange": "US", "mic": mic}, per_symbol=False)
            if not isinstance(body, list):
                raise ProviderUnavailable("/stock/symbol: unexpected response shape")
            for item in body:
                if isinstance(item, dict) and isinstance(item.get("symbol"), str):
                    names[item["symbol"]] = _text(item.get("description"))
        return names

    # --- HTTP --------------------------------------------------------------------

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
            # symbol isn't covered by the plan (as reference/finnhub.py).
            if code == 401 or (code == 403 and not per_symbol):
                raise KeyRejected(f"{path}: HTTP {code}") from None
            if code == 403:
                raise NotPermitted(f"{path}: HTTP 403") from None
            if code == 429:
                raise RateLimited(f"{path}: HTTP 429") from None
            raise ProviderUnavailable(f"{path}: HTTP {code}") from None
        except (OSError, ValueError, HTTPException) as exc:
            raise ProviderUnavailable(f"{path}: {type(exc).__name__}") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise ProviderUnavailable(f"{path}: response is not JSON") from None


def _articles(body) -> list[RawArticle]:
    if not isinstance(body, list):
        raise ProviderUnavailable("news: unexpected response shape")
    articles = []
    for item in body:
        if not isinstance(item, dict):
            continue
        url, headline = item.get("url"), _text(item.get("headline"))
        published = _timestamp(item.get("datetime"))
        if not _safe_url(url) or not headline:
            continue
        if published is None:
            continue
        articles.append(
            RawArticle(
                url=url,
                headline=headline,
                summary=_text(item.get("summary")),
                source=_text(item.get("source")),
                published_at=published,
                related=_related(item.get("related")),
            )
        )
    return articles


def _safe_url(value) -> bool:
    """Only http(s): a javascript: or data: link must never reach a report (analyze S3)."""
    if not isinstance(value, str) or text.has_unsafe(value):
        return False
    parts = urlsplit(value.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc) and value == value.strip()


def _text(value) -> str:
    """Outside text, cleaned of characters Postgres refuses (review H1)."""
    return text.clean(value).strip() if isinstance(value, str) else ""


def _related(value) -> tuple[str, ...]:
    """Finnhub's `related`: a comma-separated ticker string, possibly empty."""
    if not isinstance(value, str):
        return ()
    tags: list[str] = []
    for part in value.split(","):
        tag = part.strip().upper()
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _timestamp(value) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
