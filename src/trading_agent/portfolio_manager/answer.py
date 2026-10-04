"""Checking the model's answer (specs/008-portfolio-manager research P8; FR-009, FR-011). Pure.

The model reads text anyone can publish, so nothing it says is trusted: each item passes
the rules below, in order, or is dropped with exactly one reason. What is written comes
from the checker, not the model: the quote and its time are the run's own, a hold's size
is computed from the current weight, and report ids are mapped back from `R1`... to
database ids.

Seam for User Story 2 (tasks T024): `_RULES` lists the drop rules in research P8's order.
It holds `malformed_decision` and `unknown_symbol`; the rest (`invalid_direction`,
`no_citation`, `unknown_citation`, `unbacked_buy`, `one_sided_conflict`, `invalid_size`,
`direction_contradicts_target`, `duplicate_symbol`) are added to it there. Until then
`_build` refuses, as `malformed_decision`, any item it can't turn into a valid row, so
nothing invalid is written in the meantime.
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

DIRECTIONS = ("buy", "sell", "hold")
ELLIPSIS = "…"
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
    except (TypeError, ValueError):
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
    for index, item in enumerate(items):
        reason = next((r for rule in _RULES if (r := rule(item, given))), None)
        decision = None if reason else _build(item, given)
        if decision is not None:
            accepted.append(decision)
            continue
        symbol = item.get("symbol") if isinstance(item, dict) else None
        drops.append(
            Drop(index, symbol if isinstance(symbol, str) else None, reason or MALFORMED_DECISION)
        )
    return Checked(tuple(accepted), tuple(drops), unusable=False, received=len(items))


# --- the drop rules, in research P8's order ---------------------------------------------


def _malformed(item, given: Given) -> str | None:
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


def _unknown_symbol(item, given: Given) -> str | None:
    return None if item["symbol"] in given.symbols else UNKNOWN_SYMBOL


_RULES: tuple[Callable[[object, Given], str | None], ...] = (_malformed, _unknown_symbol)


# --- turning a passing item into a row --------------------------------------------------


def _build(item: dict, given: Given) -> CheckedDecision | None:
    symbol, direction = item["symbol"], item["direction"]
    facts = given.symbols[symbol]
    if direction not in DIRECTIONS:
        return None  # User Story 2: invalid_direction
    ids = list(dict.fromkeys(item["report_ids"]))
    if not ids or any(i not in given.refs or given.refs[i].symbol != symbol for i in ids):
        return None  # User Story 2: no_citation / unknown_citation

    if direction == "hold":
        size = min(facts.current_weight_pct, _HUNDRED).quantize(_THOUSANDTH, rounding=ROUND_DOWN)
    else:
        size = _target(item["target_weight_pct"], direction)
        if size is None:
            return None  # User Story 2: invalid_size

    reasoning = text.clean(item["reasoning"]).strip()
    return CheckedDecision(
        symbol=symbol,
        direction=direction,
        size_pct=size,
        reasoning=_cap(reasoning, given.reasoning_max_chars),
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


def _cap(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - len(ELLIPSIS)] + ELLIPSIS
