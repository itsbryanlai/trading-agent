"""Schemas and checking of the model answers (contracts/model-io.md).

Strict like the other agents' schemas: every property required, no extra keys, no nullable
types. `""` means "not given" for a query parameter. An invalid plan or answer gives `None`;
what to do about it is `service`'s decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

_QUERY_KEYS = ("name", "symbol", "agent", "from_date", "to_date", "section")

_QUERY_SCHEMA: dict = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in _QUERY_KEYS},
    "required": list(_QUERY_KEYS),
    "additionalProperties": False,
}

PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action_request": {"type": "boolean"},
        "queries": {"type": "array", "items": _QUERY_SCHEMA},
    },
    "required": ["action_request", "queries"],
    "additionalProperties": False,
}

ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {"reply": {"type": "string"}},
    "required": ["reply"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RawChoice:
    """One query the model chose, before the menu validates it. All strings."""

    name: str
    symbol: str
    agent: str
    from_date: str
    to_date: str
    section: str


@dataclass(frozen=True)
class Plan:
    action_request: bool
    choices: list[RawChoice]


def _load(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _choice(item: object) -> RawChoice | None:
    if not isinstance(item, dict) or set(item) != set(_QUERY_KEYS):
        return None
    if not all(isinstance(item[key], str) for key in _QUERY_KEYS):
        return None
    return RawChoice(**{key: item[key] for key in _QUERY_KEYS})


def parse_plan(text: str) -> Plan | None:
    data = _load(text)
    if data is None or set(data) != {"action_request", "queries"}:
        return None
    flag, queries = data["action_request"], data["queries"]
    if not isinstance(flag, bool) or not isinstance(queries, list):
        return None
    choices = [_choice(item) for item in queries]
    if any(choice is None for choice in choices):
        return None
    return Plan(flag, [c for c in choices if c is not None])


def parse_answer(text: str) -> str | None:
    data = _load(text)
    if data is None or set(data) != {"reply"}:
        return None
    reply = data["reply"]
    if not isinstance(reply, str) or not reply.strip():
        return None
    return reply
