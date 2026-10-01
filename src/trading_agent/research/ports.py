"""Research's two ports (specs/007-research-agent/contracts/ports.md).

The only ways Research reaches the network: news (and the US symbol list), and one
model call. Neither can write anything or reach a broker. Tests replace both with
fakes (tests/fakes/news.py, tests/fakes/model.py).
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

    def us_symbols(self) -> frozenset[str]: ...


# --- model -----------------------------------------------------------------------


class ModelError(Exception):
    pass


class ModelKeyRejected(ModelError):
    """401 or 403."""


class ModelRejected(ModelError):
    """Any other 4xx: a refused schema, a prompt too long, an unknown model name."""


class ModelUnavailable(ModelError):
    """Network, timeout, 429 or 5xx."""


class ModelRefused(ModelError):
    """The model declined to answer."""


class ModelTruncated(ModelError):
    """The answer hit the output limit, so it can't be trusted to be complete."""


@dataclass(frozen=True)
class ModelReply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    finish: str | None


class ModelClient(Protocol):
    def complete(self, system: str, user: str, schema: dict) -> ModelReply: ...
