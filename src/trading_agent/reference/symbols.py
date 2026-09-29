"""The day's symbol set (spec FR-001, FR-003; research D8). Pure: `now` is an argument.

Candidates come from the reference_candidate_symbols view (no time filter; D10),
so the exact window is applied here with the exchange calendar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from trading_agent.reference.normalize import INVALID_SYMBOL, Failure
from trading_agent.risk import calendar

# e.g. AAPL, BRK.B, BF-B (contracts/reference-data-interface.md "Ticker check").
TICKER_PATTERN = re.compile(r"[A-Z]{1,5}([.-][A-Z]{1,2})?")

# Most likely to be bought first (D8).
_ACTIVE, _HELD, _RECENT, _SEED = range(4)


@dataclass(frozen=True)
class Candidate:
    symbol: str
    source: str  # position | report | decision
    named_at: datetime | None
    active_until: datetime | None


@dataclass(frozen=True)
class SymbolSet:
    ordered: list[str]
    skipped: list[Failure]


def is_plausible_ticker(symbol) -> bool:
    return isinstance(symbol, str) and TICKER_PATTERN.fullmatch(symbol) is not None


def window_start(now: datetime) -> datetime:
    """The previous session's open: the start of "recently named" (FR-001)."""
    today = calendar.trading_day(now)
    return calendar.open_time(calendar.previous_session(today))


def _priority(candidate: Candidate, now: datetime, start: datetime) -> int | None:
    if candidate.source == "position":
        return _HELD
    named = candidate.named_at
    if candidate.source == "report":
        if candidate.active_until is not None and candidate.active_until > now:
            return _ACTIVE
        return _RECENT if named is not None and named >= start else None
    if candidate.source == "decision" and named is not None:
        if calendar.trading_day(named) == calendar.trading_day(now) and named <= now:
            return _ACTIVE
        return _RECENT if named >= start else None
    return None


def build_symbol_set(candidates, seeds, now: datetime) -> SymbolSet:
    if now.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    start = window_start(now)
    best: dict[str, int] = {}
    for candidate in candidates:
        priority = _priority(candidate, now, start)
        if priority is not None:
            best[candidate.symbol] = min(priority, best.get(candidate.symbol, priority))
    for seed in seeds:
        best.setdefault(seed, _SEED)

    skipped = sorted({s for s in best if not is_plausible_ticker(s)}, key=str)
    ordered = sorted((s for s in best if is_plausible_ticker(s)), key=lambda s: (best[s], s))
    return SymbolSet(ordered=ordered, skipped=[Failure(s, INVALID_SYMBOL) for s in skipped])
