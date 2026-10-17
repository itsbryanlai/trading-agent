"""The row's state: encode and decode `per_agent_attribution` (research J11).

Pure. Numbers are JSON strings of decimals. Rounding happens here and only here, once, at
storage (research J5 step 7): weights down to 6 places so stored weights never sum over 100,
the index and the scale factor to 8, returns to 6; prices are written as received. The next
run reads the rounded values, so a run is a function of the previous row and today's inputs.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any

from trading_agent.journal.model import SYMBOL_PATTERN, Book, BookResult, Holding

SCHEMA_VERSION = 1
MISSED_LISTED = 30

_WEIGHT = Decimal("1e-6")
_INDEX = Decimal("1e-8")
_RETURN = Decimal("1e-6")


class UnknownSchema(Exception):
    """The stored object has a schema version, or a shape, this code does not know."""


def encode(
    *,
    day: date,
    previous_day: date | None,
    sessions_covered: int,
    missed_sessions: tuple[date, ...],
    holding_sessions: int,
    summary_version: str,
    account_return: Decimal | None,
    account_base: str,
    close_taken_at: datetime | None,
    results: Mapping[str, BookResult],
    usage: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    """The attribution object (contracts/attribution.md). Agents and symbols are sorted."""
    return {
        "schema_version": SCHEMA_VERSION,
        "trading_day": day.isoformat(),
        "previous_trading_day": None if previous_day is None else previous_day.isoformat(),
        "sessions_covered": sessions_covered,
        "missed_sessions": [d.isoformat() for d in missed_sessions[:MISSED_LISTED]],
        "holding_sessions": holding_sessions,
        "summary_version": summary_version,
        "account": {
            "return": None if account_return is None else _fixed(account_return, _RETURN),
            "base": account_base,
            "close_taken_at": None if close_taken_at is None else close_taken_at.isoformat(),
        },
        "agents": {
            agent: _agent(results[agent], dict(usage.get(agent, {}))) for agent in sorted(results)
        },
    }


def decode_books(attribution: Mapping[str, Any]) -> dict[str, Book]:
    """The books a previous row left behind. Any other schema version, and any value no run
    could have stored (a non-finite number, a weight outside 0 to 100 or a book over 100, a
    non-positive price or index, a date after the row's day, a malformed symbol), is refused
    (research J11)."""
    try:
        if attribution["schema_version"] != SCHEMA_VERSION or isinstance(
            attribution["schema_version"], bool
        ):
            raise UnknownSchema(f"schema_version {attribution['schema_version']!r}")
        day = date.fromisoformat(attribution["trading_day"])
        return {agent: _book(agent, body, day) for agent, body in attribution["agents"].items()}
    except UnknownSchema:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation) as exc:
        raise UnknownSchema(f"malformed attribution: {type(exc).__name__}") from exc


def _agent(result: BookResult, usage: dict[str, int]) -> dict[str, Any]:
    book = result.book
    return {
        "started_on": book.started_on.isoformat(),
        "index": _fixed(book.index, _INDEX),
        "day_return": _fixed(result.day_return, _RETURN),
        "holdings": {
            symbol: {
                "weight_pct": _fixed(h.weight_pct, _WEIGHT, ROUND_DOWN),
                "ref_price": format(h.ref_price, "f"),
                "support_session": h.support_session.isoformat(),
            }
            for symbol, h in sorted(book.holdings.items())
        },
        "scaled_by": None if result.scaled_by is None else _fixed(result.scaled_by, _INDEX),
        "late_reports": result.late_reports,
        "unpriced": sorted(result.unpriced),
        "skipped_targets": sorted(result.skipped_targets),
        "exited": {
            "sell": sorted(result.exited_sell),
            "holding_limit": sorted(result.exited_holding_limit),
        },
        "usage": usage,
    }


def _book(agent: str, body: Mapping[str, Any], day: date) -> Book:
    holdings = {}
    for symbol, h in body["holdings"].items():
        if SYMBOL_PATTERN.fullmatch(symbol) is None:
            raise UnknownSchema("malformed symbol")
        weight = _number(h["weight_pct"])
        if not 0 <= weight <= 100:
            raise UnknownSchema("weight out of range")
        support = date.fromisoformat(h["support_session"])
        if support > day:
            raise UnknownSchema("support session after the row's day")
        holdings[symbol] = Holding(symbol, weight, _positive(h["ref_price"]), support)
    if sum((h.weight_pct for h in holdings.values()), Decimal(0)) > 100:
        raise UnknownSchema("weights sum over 100")
    started = date.fromisoformat(body["started_on"])
    if started > day:
        raise UnknownSchema("book started after the row's day")
    return Book(agent, started, _positive(body["index"]), holdings)


def _number(text: str) -> Decimal:
    value = Decimal(text)
    if not value.is_finite():
        raise UnknownSchema("non-finite number")
    return value


def _positive(text: str) -> Decimal:
    value = _number(text)
    if value <= 0:
        raise UnknownSchema("non-positive price or index")
    return value


def _fixed(value: Decimal, step: Decimal, rounding: str = ROUND_HALF_EVEN) -> str:
    with localcontext() as ctx:
        ctx.prec = 60
        rounded = value.quantize(step, rounding=rounding)
    return format(abs(rounded) if not rounded else rounded, "f")
