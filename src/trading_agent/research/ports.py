"""Research's news port (specs/007-research-agent/contracts/ports.md).

The way Research reaches the network for news (and the US symbol list). It can't
write anything or reach a broker. The model port lives in trading_agent.llm. Tests
replace this with tests/fakes/news.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

# --- news ------------------------------------------------------------------------


class NewsError(Exception):
    pass


class KeyRejected(NewsError):
    """401, or 403 on a call that isn't per symbol: the key itself was refused."""


class NotPermitted(NewsError):
    """403 on one symbol's company news: that symbol only."""


class RateLimited(NewsError):
    """429."""


class ProviderUnavailable(NewsError):
    """Timeout, network or server error, or an unreadable response."""


@dataclass(frozen=True)
class RawArticle:
    url: str
    headline: str
    summary: str
    source: str
    published_at: datetime
    related: tuple[str, ...]


class NewsSource(Protocol):
    def general_news(self) -> list[RawArticle]: ...

    def company_news(self, symbol: str, start: date, end: date) -> list[RawArticle]: ...

    def us_symbols(self) -> dict[str, str]:
        """US-listed symbols, each with its company name ("" when unknown)."""
        ...
