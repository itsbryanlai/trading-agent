"""The journal writer's plain values (specs/012 data-model.md "In memory").

Frozen dataclasses only, no logic. Money, weights and prices are `Decimal`, never `float`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Why a symbol has no price for the day (research J2, J8).
UNPRICED_REASONS = (
    "not_today",
    "no_price",
    "not_permitted",
    "rate_limited",
    "unavailable",
    "deadline",
    "malformed",
)


@dataclass(frozen=True)
class Holding:
    symbol: str
    weight_pct: Decimal
    ref_price: Decimal
    support_session: date


@dataclass(frozen=True)
class Book:
    agent: str
    started_on: date
    index: Decimal
    holdings: dict[str, Holding]


@dataclass(frozen=True)
class ReportRow:
    """A report in the window. `support_session` and `late` are set by the service (J4)."""

    id: int
    agent: str
    generated_at: datetime
    symbol: str
    direction: str
    suggested_size_pct: Decimal | None
    support_session: date
    late: bool


@dataclass(frozen=True)
class Price:
    symbol: str
    close: Decimal | None
    reason: str | None


@dataclass(frozen=True)
class BookResult:
    """One agent's book after the day, with what happened on the way (research J5)."""

    book: Book
    day_return: Decimal
    scaled_by: Decimal | None
    late_reports: int
    unpriced: tuple[str, ...]
    skipped_targets: tuple[str, ...]
    exited_sell: tuple[str, ...]
    exited_holding_limit: tuple[str, ...]


@dataclass(frozen=True)
class DayFacts:
    """Everything the summary shows: numbers, dates, closed-set codes and tickers (J10)."""

    day: date
    equity_open: Decimal
    equity_close: Decimal
    close_taken_at: datetime | None
    breaker_triggered: bool
    decisions: int
    by_direction: dict[str, int]
    approved: int
    rejected: int
    rejection_rules: dict[str, int]
    orders: int
    order_statuses: dict[str, int]
    refusals: dict[str, int]
    stop_loss_triggers: int
    stop_loss_approved: int
    stop_loss_rejected: int
    decided_symbols: tuple[str, ...]
    malformed_symbols: int
    missed_sessions: tuple[date, ...]
    unpriced_count: int


@dataclass(frozen=True)
class JournalRow:
    """The row to write: the `journal` columns apart from `written_at`."""

    trading_day: date
    equity_open: Decimal
    equity_close: Decimal
    summary_md: str
    per_agent_attribution: dict[str, Any]


@dataclass(frozen=True)
class RunOutcome:
    """`wrote`, `nothing_to_do` or `failed`, with a reason (contracts/journal-interface.md)."""

    status: str
    reason: str | None = None
    row: JournalRow | None = field(default=None)
