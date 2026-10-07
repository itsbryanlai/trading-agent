"""Checking the model's answer and building the rows (specs/011 research O8, O9). Pure.

The model reads text a provider controls (company names, industries), so nothing it says is
trusted: a proposal is written only if it passes every rule, and every field that matters
is rebuilt by code. A report's sources are built from what the run fetched, never from the
model's text (FR-013). The dry-run and the write both go through here.

A proposal is dropped, with exactly one reason, at the first rule it fails, in the order
below. SC-002 ("nothing off the shortlist is written") is a Hypothesis property
of this module (tests/unit/opportunistic_identifier/test_answer_property.py).
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from trading_agent.opportunistic_identifier import text
from trading_agent.opportunistic_identifier.screen import NameData
from trading_agent.risk import calendar

# The drop reasons, in the order the rules apply (a closed set; contracts/oi-interface.md).
MALFORMED_ANSWER = "malformed_answer"
NOT_SHORTLISTED = "not_shortlisted"
INVALID_DIRECTION = "invalid_direction"
INVALID_CONVICTION = "invalid_conviction"
INVALID_SIZE = "invalid_size"
DUPLICATE_SYMBOL = "duplicate_symbol"
ALREADY_OPEN = "already_open"
DROP_REASONS = (
    MALFORMED_ANSWER,
    NOT_SHORTLISTED,
    INVALID_DIRECTION,
    INVALID_CONVICTION,
    INVALID_SIZE,
    DUPLICATE_SYMBOL,
    ALREADY_OPEN,
)
SYMBOL_LOG_CHARS = 16  # a model-written symbol reaches a log only cleaned and this short

ELLIPSIS = "…"
# Where the sources point. The adapter's BASE_URL (a test keeps them equal): this module is
# pure and doesn't import the adapter.
SOURCE_BASE_URL = "https://finnhub.io/api/v1"
PUBLISHER = "Finnhub"
_THOUSANDTH = Decimal("0.001")
_HUNDRED = Decimal(100)

# One table, from which the schema sent to the provider is generated, so the prompt's schema
# and this checker can't drift apart. Only keywords both providers accept in strict mode
# (types, enum, required, additionalProperties); ranges are checked in `_proposal`.
_FIELDS: dict[str, dict] = {
    "symbol": {"type": "string"},
    "direction": {"type": "string", "enum": ["buy"]},
    "conviction": {"type": "integer"},
    "suggested_size_pct": {"type": "number"},
    "rationale": {"type": "string"},
}
ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _FIELDS,
                "required": list(_FIELDS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["proposals"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ReportRow:
    """One `reports` row. Defined here, not in service.py, so this module stays pure."""

    symbol: str | None
    direction: str
    conviction: int | None
    suggested_size_pct: Decimal | None
    sources: list[dict]
    rationale_md: str
    expires_at: datetime


@dataclass(frozen=True)
class Proposal:
    symbol: str
    conviction: int
    size: Decimal
    rationale: str


@dataclass(frozen=True)
class Drop:
    index: int
    symbol: str | None
    reason: str


@dataclass(frozen=True)
class Checked:
    reports: tuple[Proposal, ...]
    drops: tuple[Drop, ...]
    unusable: bool  # the answer as a whole isn't {"proposals": [...]}
    received: int  # proposals in the answer


def check(
    answer: str,
    shortlist: Sequence[str],
    open_symbols: Collection[str],
    rationale_max_chars: int,
) -> Checked:
    try:
        parsed = json.loads(answer)
    except (TypeError, ValueError):
        return Checked((), (), unusable=True, received=0)
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"proposals"}
        or not isinstance(parsed["proposals"], list)
    ):
        return Checked((), (), unusable=True, received=0)

    allowed = set(shortlist)
    accepted: list[Proposal] = []
    drops: list[Drop] = []
    for index, item in enumerate(parsed["proposals"]):
        found = _proposal(item, allowed, rationale_max_chars)
        if isinstance(found, Drop):
            drops.append(replace(found, index=index))
        elif any(p.symbol == found.symbol for p in accepted):
            drops.append(Drop(index, found.symbol, DUPLICATE_SYMBOL))
        elif found.symbol in open_symbols:
            drops.append(Drop(index, found.symbol, ALREADY_OPEN))
        else:
            accepted.append(found)
    return Checked(tuple(accepted), tuple(drops), unusable=False, received=len(parsed["proposals"]))


def _proposal(item, shortlist: set[str], rationale_max_chars: int) -> Proposal | Drop:
    """The proposal, or the first rule it fails (its index is filled in by the caller)."""
    if not isinstance(item, dict):
        return Drop(0, None, MALFORMED_ANSWER)
    symbol = item.get("symbol")
    named = _loggable(symbol) if isinstance(symbol, str) else None
    rationale = item.get("rationale")
    if set(item) != set(_FIELDS) or not isinstance(symbol, str) or not isinstance(rationale, str):
        return Drop(0, named, MALFORMED_ANSWER)
    rationale = text.clean(rationale).strip()
    if not rationale:
        return Drop(0, named, MALFORMED_ANSWER)
    if symbol not in shortlist:  # exact string match: " MSFT" and "msft" are not "MSFT"
        return Drop(0, named, NOT_SHORTLISTED)
    if item["direction"] != "buy":
        return Drop(0, named, INVALID_DIRECTION)
    conviction = item["conviction"]
    if isinstance(conviction, bool) or not isinstance(conviction, int) or not 1 <= conviction <= 5:
        return Drop(0, named, INVALID_CONVICTION)
    size = _size(item["suggested_size_pct"])
    if size is None:
        return Drop(0, named, INVALID_SIZE)
    return Proposal(symbol, conviction, size, _cap(rationale, rationale_max_chars))


def _loggable(symbol: str) -> str:
    return text.clean(symbol)[:SYMBOL_LOG_CHARS]


def rows(
    checked: Checked, data_by_symbol: Mapping[str, NameData], trading_day: date
) -> list[ReportRow]:
    """One buy row per accepted proposal, expiring at the trading day's close."""
    expires_at = calendar.close_time(trading_day)
    return [
        ReportRow(
            symbol=p.symbol,
            direction="buy",
            conviction=p.conviction,
            suggested_size_pct=p.size,
            sources=_sources(data_by_symbol[p.symbol]),
            rationale_md=p.rationale,
            expires_at=expires_at,
        )
        for p in checked.reports
    ]


def _sources(data: NameData) -> list[dict]:
    """The three endpoints fetched for this name, with no key: it travels in a header."""
    symbol = data.symbol
    fetched = data.fetched_at.isoformat()
    quoted = data.quote.timestamp.isoformat() if data.quote.timestamp else fetched
    parts = (
        ("quote", f"/quote?symbol={symbol}", quoted),
        ("company profile", f"/stock/profile2?symbol={symbol}", fetched),
        ("basic financials", f"/stock/metric?symbol={symbol}&metric=all", fetched),
    )
    return [
        {
            "title": f"{PUBLISHER} {what} for {symbol}",
            "url": f"{SOURCE_BASE_URL}{path}",
            "publisher": PUBLISHER,
            "published_at": when,
            "relevance": "primary",
        }
        for what, path, when in parts
    ]


def _size(value) -> Decimal | None:
    """A target weight, rounded down to 3 places (the column is numeric(6,3)): above 0 once
    rounded, at most 100. Research's buy rule, copied."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        exact = Decimal(str(value))
    except InvalidOperation:
        return None
    if exact > _HUNDRED or exact <= 0:
        return None
    size = exact.quantize(_THOUSANDTH, rounding=ROUND_DOWN)
    return size if size > 0 else None


def _cap(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - len(ELLIPSIS)] + ELLIPSIS
