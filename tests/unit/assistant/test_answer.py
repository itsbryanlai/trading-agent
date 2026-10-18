"""Schemas and parsing of the model answers (specs/013 T008, contracts/model-io.md)."""

from __future__ import annotations

import json

import pytest

from trading_agent.assistant.answer import (
    ANSWER_SCHEMA,
    PLAN_SCHEMA,
    Plan,
    RawChoice,
    parse_answer,
    parse_plan,
)

QUERY = {
    "name": "positions_now",
    "symbol": "",
    "agent": "",
    "from_date": "",
    "to_date": "",
    "section": "",
}


def _plan(**over):
    doc = {"action_request": False, "queries": [dict(QUERY)]}
    doc.update(over)
    return json.dumps(doc)


def _strict(schema):
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])
    for prop in schema["properties"].values():
        assert not isinstance(prop["type"], list)  # no nullable types
        if prop["type"] == "object":
            _strict(prop)
        if prop["type"] == "array" and prop["items"]["type"] == "object":
            _strict(prop["items"])


def test_schemas_are_strict():
    _strict(PLAN_SCHEMA)
    _strict(ANSWER_SCHEMA)
    assert PLAN_SCHEMA["properties"]["action_request"] == {"type": "boolean"}
    item = PLAN_SCHEMA["properties"]["queries"]["items"]
    assert set(item["properties"]) == set(QUERY)
    assert all(p == {"type": "string"} for p in item["properties"].values())


def test_valid_plan():
    assert parse_plan(_plan()) == Plan(False, [RawChoice(**QUERY)])
    assert parse_plan(_plan(action_request=True, queries=[])) == Plan(True, [])


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        "null",
        json.dumps({"queries": []}),  # action_request missing
        json.dumps({"action_request": "true", "queries": []}),
        json.dumps({"action_request": None, "queries": []}),
        json.dumps({"action_request": 1, "queries": []}),
        json.dumps({"action_request": False}),  # queries missing
        json.dumps({"action_request": False, "queries": [], "extra": 1}),
        json.dumps({"action_request": False, "queries": "positions_now"}),
        json.dumps({"action_request": False, "queries": [{**QUERY, "extra": "x"}]}),
        json.dumps({"action_request": False, "queries": [{"name": "positions_now"}]}),
        json.dumps({"action_request": False, "queries": [{**QUERY, "symbol": None}]}),
        json.dumps({"action_request": False, "queries": [{**QUERY, "symbol": 5}]}),
        json.dumps({"action_request": False, "queries": ["positions_now"]}),
    ],
)
def test_invalid_plans_are_none(text):
    assert parse_plan(text) is None


def test_valid_answer():
    assert parse_answer(json.dumps({"reply": "Hello"})) == "Hello"


@pytest.mark.parametrize(
    "text",
    [
        "nope",
        json.dumps({"reply": ""}),
        json.dumps({"reply": "   \n"}),
        json.dumps({"reply": 5}),
        json.dumps({"reply": None}),
        json.dumps({}),
        json.dumps({"reply": "x", "extra": 1}),
        json.dumps(["x"]),
    ],
)
def test_invalid_answers_are_none(text):
    assert parse_answer(text) is None
