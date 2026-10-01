"""Choosing the run's articles (specs/007-research-agent research R4). Pure: `now` is
an argument.

The same news always gives the same selection: window, per-feed caps (newest first,
ties on the URL), duplicates merged into their first appearance, identifiers in the
final order. A watchlist symbol's own feed tags its articles with that symbol, which
the relevance check (answer.py, `uncited_symbol`) relies on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_agent.research.config import ResearchConfig
from trading_agent.research.ports import RawArticle
from trading_agent.risk import calendar


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    url: str
    publisher: str
    published_at: datetime
    related: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class Selection:
    articles: tuple[Article, ...]
    in_window: int  # distinct articles in the window, before the caps


def window_start(now: datetime) -> datetime:
    """The previous session's close, so Monday's run covers the weekend."""
    return calendar.close_time(calendar.previous_session(calendar.trading_day(now)))


def _newest_first(items: list[RawArticle], cap: int) -> list[RawArticle]:
    ordered = sorted(items, key=lambda a: (-a.published_at.timestamp(), a.url))
    return ordered[:cap]


def select(
    general: list[RawArticle],
    by_symbol: dict[str, list[RawArticle]],
    now: datetime,
    cfg: ResearchConfig,
) -> Selection:
    start = window_start(now)

    def in_window(items):
        return [a for a in items if start <= a.published_at <= now]

    general_in = in_window(general)
    feeds: list[tuple[str | None, list[RawArticle]]] = [
        (None, _newest_first(general_in, cfg.general_news_max_articles))
    ]
    seen_urls = {a.url for a in general_in}
    for symbol in cfg.watchlist:
        items = in_window(by_symbol.get(symbol, []))
        seen_urls.update(a.url for a in items)
        feeds.append((symbol, _newest_first(items, cfg.articles_per_symbol)))

    merged: dict[str, tuple[RawArticle, list[str]]] = {}
    for feed_symbol, items in feeds:
        for raw in items:
            tags = [*raw.related, *([feed_symbol] if feed_symbol else [])]
            if raw.url in merged:
                first, existing = merged[raw.url]
                existing.extend(t for t in tags if t not in existing)
            else:
                unique: list[str] = []
                for tag in tags:
                    if tag not in unique:
                        unique.append(tag)
                merged[raw.url] = (raw, unique)

    articles = tuple(
        Article(
            id=f"A{index}",
            title=raw.headline,
            url=raw.url,
            publisher=raw.source,
            published_at=raw.published_at,
            related=tuple(tags),
            summary=raw.summary[: cfg.article_summary_max_chars],
        )
        for index, (raw, tags) in enumerate(merged.values(), start=1)
    )
    return Selection(articles=articles, in_window=len(seen_urls))
