"""Shared builders for Research's unit tests (specs/007-research-agent tasks.md conventions).

Test clock: Thursday 2026-10-01, EDT (UTC-4). The previous session closed on
Wednesday 2026-09-30 at 16:00 ET (20:00 UTC); today's close is 20:00 UTC.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime, timedelta

from trading_agent.research.config import DEFAULT_CONFIG_PATH, load_config
from trading_agent.research.ports import RawArticle

THU_0830 = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)  # 08:30 ET
PREVIOUS_CLOSE = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)
THU_CLOSE = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
MON_0830 = datetime(2026, 10, 5, 12, 30, tzinfo=UTC)
SAT_0830 = datetime(2026, 10, 3, 12, 30, tzinfo=UTC)
HOLIDAY_0830 = datetime(2026, 11, 26, 13, 30, tzinfo=UTC)  # Thanksgiving, EST
EARLY_CLOSE_DAY_0830 = datetime(2026, 11, 27, 13, 30, tzinfo=UTC)
EARLY_CLOSE = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


def config(watchlist=(), **changes):
    """The shipped config with a watchlist and top-level or model.<field> overrides."""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    model_changes = {k[6:]: v for k, v in changes.items() if k.startswith("model_")}
    top = {k: v for k, v in changes.items() if not k.startswith("model_")}
    model = dataclasses.replace(cfg.model, **model_changes)
    return dataclasses.replace(cfg, watchlist=tuple(watchlist), model=model, **top)


def article(
    name: str,
    *,
    at: datetime = THU_0830 - timedelta(hours=2),
    related: tuple[str, ...] = (),
    summary: str | None = None,
    source: str = "Example Wire",
) -> RawArticle:
    return RawArticle(
        url=f"https://news.example.com/{name}",
        headline=f"Headline {name}",
        summary=summary if summary is not None else f"Summary of {name}.",
        source=source,
        published_at=at,
        related=related,
    )


def proposal(symbol="AAPL", direction="buy", conviction=3, size=4, ids=("A1",), rationale=None):
    return {
        "symbol": symbol,
        "direction": direction,
        "conviction": conviction,
        "suggested_size_pct": size,
        "rationale": rationale if rationale is not None else f"Thesis for {symbol}.",
        "article_ids": list(ids),
    }


class Clock:
    """A test clock whose monotonic time advances when the code sleeps."""

    def __init__(self, now: datetime = THU_0830) -> None:
        self.now = now
        self.mono = 0.0
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.mono += seconds

    def monotonic(self) -> float:
        return self.mono


def messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.startswith("trading_agent.research")]


LOGGER = logging.getLogger("trading_agent.research")
