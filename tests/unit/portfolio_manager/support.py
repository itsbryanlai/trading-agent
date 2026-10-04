"""Builders for the Portfolio Manager's unit tests (specs/008-portfolio-manager tasks.md,
"Conventions": test clock Thursday 2026-10-01, EDT, 10:00 ET = 14:00 UTC)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_agent.reference.provider import Quote

OPEN = datetime(2026, 10, 1, 13, 30, tzinfo=UTC)  # 09:30 ET
NOW = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)  # 10:00 ET
MAX_AGE = timedelta(minutes=5)

SOURCE = {
    "title": "Q3 earnings beat",
    "url": "https://news.example.com/aapl-q3",
    "publisher": "Example Wire",
    "published_at": "2026-10-01T12:00:00+00:00",
    "relevance": "primary",
}


def quote(symbol="AAPL", current="200", at=NOW - timedelta(minutes=1)) -> Quote:
    return Quote(symbol, None if current is None else Decimal(current), Decimal("199"), at)
