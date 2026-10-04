"""Checking the model's answer (specs/008-portfolio-manager research P8; FR-009, FR-011). Pure.

The model reads text anyone can publish, so nothing it says is trusted: each item passes
the rules below, in order, or is dropped with exactly one reason. What is written comes
from the checker, not the model: the quote and its time are the run's own, a hold's size
is computed from the current weight, and report ids are mapped back from `R1`... to
database ids.

`_RULES` lists the drop rules in research P8's order; an item gets the first reason that
applies, and `_build` runs only on an item every rule accepted.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from trading_agent.portfolio_manager import text
from trading_agent.portfolio_manager.inputs import Given

MALFORMED_DECISION = "malformed_decision"
UNKNOWN_SYMBOL = "unknown_symbol"
INVALID_DIRECTION = "invalid_direction"
NO_CITATION = "no_citation"
UNKNOWN_CITATION = "unknown_citation"
UNBACKED_BUY = "unbacked_buy"
ONE_SIDED_CONFLICT = "one_sided_conflict"
INVALID_SIZE = "invalid_size"
DIRECTION_CONTRADICTS_TARGET = "direction_contradicts_target"
DUPLICATE_SYMBOL = "duplicate_symbol"
# The closed set of contracts/pm-interface.md, in the order the rules apply.
DROP_REASONS = (
    MALFORMED_DECISION,
    UNKNOWN_SYMBOL,
    INVALID_DIRECTION,
    NO_CITATION,
    UNKNOWN_CITATION,
    UNBACKED_BUY,
    ONE_SIDED_CONFLICT,
    INVALID_SIZE,
    DIRECTION_CONTRADICTS_TARGET,
    DUPLICATE_SYMBOL,
)

DIRECTIONS = ("buy", "sell", "hold")
_THOUSANDTH = Decimal("0.001")
_HUNDRED = Decimal(100)

# One table, from which the schema sent to the provider is generated, so the prompt's
# schema and this checker can't drift apart. Only keywords both providers accept in
# strict mode (types, enum, required, additionalProperties); ranges are checked here.
_FIELDS: dict[str, dict] = {
    "symbol": {"type": "string"},
    "direction": {"type": "string", "enum": list(DIRECTIONS)},
    "target_weight_pct": {"type": "number"},
    "reasoning": {"type": "string"},
    "report_ids": {"type": "array", "items": {"type": "string"}},
}
ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _FIELDS,
                "required": list(_FIELDS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CheckedDecision:
    symbol: str
    direction: str
    size_pct: Decimal
    reasoning: str
    quote: Decimal
    quote_time: datetime
    report_ids: tuple[str, ...]  # database ids, de-duplicated, in the model's order


@dataclass(frozen=True)
class Drop:
    index: int
    symbol: str | None
    reason: str


@dataclass(frozen=True)
class Checked:
    decisions: tuple[CheckedDecision, ...]
    drops: tuple[Drop, ...]
    unusable: bool  # the answer as a whole isn't {"decisions": [...]}
    received: int  # items in the answer


def check(answer_text: str, given: Given) -> Checked:
    try:
        answer = json.loads(answer_text)
    except (TypeError, ValueError, RecursionError):
        return Checked((), (), unusable=True, received=0)
    if (
        not isinstance(answer, dict)
        or set(answer) != {"decisions"}
        or not isinstance(answer["decisions"], list)
    ):
        return Checked((), (), unusable=True, received=0)

    items = answer["decisions"]
    accepted: list[CheckedDecision] = []
    drops: list[Drop] = []
    decided: set[str] = set()
    for index, item in enumerate(items):
        reason = next((r for rule in _RULES if (r := rule(item, given, decided))), None)
        if reason is None:
            decision = _build(item, given)
            accepted.append(decision)
            decided.add(decision.symbol)
            continue
        symbol = item.get("symbol") if isinstance(item, dict) else None
        drops.append(Drop(index, symbol if isinstance(symbol, str) else None, reason))
    return Checked(tuple(accepted), tuple(drops), unusable=False, received=len(items))


# --- the drop rules, in research P8's order ---------------------------------------------
# Each takes the item, what was given and the symbols already decided in this answer, and
# returns its reason or None. A rule may rely on every rule before it having passed.


def _malformed(item, given: Given, decided: set[str]) -> str | None:
    if not isinstance(item, dict) or set(item) != set(_FIELDS):
        return MALFORMED_DECISION
    target = item["target_weight_pct"]
    ids = item["report_ids"]
    ok = (
        isinstance(item["symbol"], str)
        and isinstance(item["direction"], str)
        and isinstance(item["reasoning"], str)
        and not isinstance(target, bool)
        and isinstance(target, int | float)
        and isinstance(ids, list)
        and all(isinstance(i, str) for i in ids)
    )
    return None if ok else MALFORMED_DECISION


def _unknown_symbol(item, given: Given, decided: set[str]) -> str | None:
    return None if item["symbol"] in given.symbols else UNKNOWN_SYMBOL


def _invalid_direction(item, given: Given, decided: set[str]) -> str | None:
    return None if item["direction"] in DIRECTIONS else INVALID_DIRECTION


def _no_citation(item, given: Given, decided: set[str]) -> str | None:
    return None if item["report_ids"] else NO_CITATION


def _unknown_citation(item, given: Given, decided: set[str]) -> str | None:
    """Every id must be one given in this run, for this symbol (SC-001)."""
    for report_id in item["report_ids"]:
        ref = given.refs.get(report_id)
        if ref is None or ref.symbol != item["symbol"]:
            return UNKNOWN_CITATION
    return None


def _cited_sides(item, given: Given) -> set[str]:
    return {given.refs[i].direction for i in item["report_ids"]}


def _unbacked_buy(item, given: Given, decided: set[str]) -> str | None:
    """A buy needs a report arguing buy, whatever a rationale said (SC-008)."""
    if item["direction"] == "buy" and "buy" not in _cited_sides(item, given):
        return UNBACKED_BUY
    return None


def _one_sided_conflict(item, given: Given, decided: set[str]) -> str | None:
    """When the reports given for a symbol disagree, the decision must answer both (SC-003)."""
    given_sides = {r.direction for r in given.refs.values() if r.symbol == item["symbol"]}
    if {"buy", "sell"} <= given_sides and not {"buy", "sell"} <= _cited_sides(item, given):
        return ONE_SIDED_CONFLICT
    return None


def _invalid_size(item, given: Given, decided: set[str]) -> str | None:
    if item["direction"] == "hold":
        return None  # a hold's size is ignored
    size = _target(item["target_weight_pct"], item["direction"])
    return INVALID_SIZE if size is None else None


def _direction_contradicts_target(item, given: Given, decided: set[str]) -> str | None:
    """A buy ends above the current weight and a sell below it. A sell on a symbol not
    held always fails: its weight is 0."""
    direction = item["direction"]
    if direction == "hold":
        return None
    size = _target(item["target_weight_pct"], direction)
    weight = given.symbols[item["symbol"]].current_weight_pct
    if (direction == "buy" and size <= weight) or (direction == "sell" and size >= weight):
        return DIRECTION_CONTRADICTS_TARGET
    return None


def _duplicate_symbol(item, given: Given, decided: set[str]) -> str | None:
    return DUPLICATE_SYMBOL if item["symbol"] in decided else None


_RULES: tuple[Callable[[object, Given, set[str]], str | None], ...] = (
    _malformed,
    _unknown_symbol,
    _invalid_direction,
    _no_citation,
    _unknown_citation,
    _unbacked_buy,
    _one_sided_conflict,
    _invalid_size,
    _direction_contradicts_target,
    _duplicate_symbol,
)


# --- turning an accepted item into a row ------------------------------------------------


def _build(item: dict, given: Given) -> CheckedDecision:
    symbol, direction = item["symbol"], item["direction"]
    facts = given.symbols[symbol]
    ids = list(dict.fromkeys(item["report_ids"]))
    if direction == "hold":
        size = min(facts.current_weight_pct, _HUNDRED).quantize(_THOUSANDTH, rounding=ROUND_DOWN)
    else:
        size = _target(item["target_weight_pct"], direction)

    reasoning = text.clean(item["reasoning"]).strip()
    return CheckedDecision(
        symbol=symbol,
        direction=direction,
        size_pct=size,
        reasoning=text.cap(reasoning, given.reasoning_max_chars),
        quote=facts.quote,
        quote_time=facts.quote_time,
        report_ids=tuple(given.refs[i].report_id for i in ids),
    )


def _target(value, direction: str) -> Decimal | None:
    """A target weight in 0-100, rounded down to 3 places. A buy needs more than 0, and
    a tiny sell must not round down to a full exit."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        exact = Decimal(str(value))
    except InvalidOperation:
        return None
    if exact < 0 or exact > _HUNDRED:
        return None
    size = exact.quantize(_THOUSANDTH, rounding=ROUND_DOWN)
    if size == 0 and (exact != 0 or direction == "buy"):
        return None
    return size
