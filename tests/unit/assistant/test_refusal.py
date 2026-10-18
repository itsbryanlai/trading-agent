"""The action refusal (specs/013 tasks T019; research A8; ADR 0024 §2, ADR 0025 §2)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.fakes.assistant_store import FakeStore
from tests.fakes.model import FakeModel
from trading_agent.assistant import replies
from trading_agent.assistant.config import load_config
from trading_agent.assistant.memory import Memory
from trading_agent.assistant.prompt import REFUSAL_SYSTEM
from trading_agent.assistant.service import respond
from trading_agent.llm.ports import ModelUnavailable

NOW = datetime(2026, 10, 20, 7, 0, tzinfo=ZoneInfo("Asia/Singapore"))
CONFIG = load_config()
LOG = "trading_agent.assistant"
PLAN_Q = {
    "name": "portfolio_now",
    "symbol": "",
    "agent": "",
    "from_date": "",
    "to_date": "",
    "section": "",
}


def update(text):
    return {
        "update_id": 1,
        "message": {"message_id": 1, "text": text, "chat": {"id": 4242, "type": "private"}},
    }


def go(text, *, plan=None, reply="Ask it as a question instead.", answer=None, store=None):
    store = store if store is not None else FakeStore()
    plan_model = FakeModel(plan if plan is not None else {"action_request": False, "queries": []})
    answer_model = answer or FakeModel({"reply": reply})
    memory = Memory(5, 30)
    handled = respond(
        update(text),
        now=NOW,
        config=CONFIG,
        store=store,
        plan_model=plan_model,
        answer_model=answer_model,
        memory=memory,
    )
    return handled, store, plan_model, answer_model, memory


def flagged(*queries):
    return {"action_request": True, "queries": list(queries)}


@pytest.mark.parametrize(
    "text, word",
    [("pause trading", "pause"), ("sell AAPL", "sell"), ("  CANCEL the order", "cancel")],
)
def test_a_keyword_refusal_reads_nothing_and_calls_no_plan(text, word):
    handled, store, plan_model, answer_model, memory = go(text)
    assert store.known_calls == 0 and store.run_calls == []
    assert plan_model.calls == []
    assert len(answer_model.calls) == 1
    call = answer_model.calls[0]
    assert call["system"] == REFUSAL_SYSTEM
    doc = json.loads(call["user"])
    assert set(doc) == {"message", "trigger", "matched_word", "action_words", "controls"}
    assert doc["trigger"] == "keyword" and doc["matched_word"] == word
    assert doc["message"] == text.strip()
    assert doc["action_words"] == list(CONFIG.action_words)
    assert doc["controls"] == list(replies.CONTROLS)
    assert memory.recent(NOW) == []
    assert handled.outcome.kind == "refused" and handled.outcome.detail == "explained"


def test_the_text_is_the_fixed_line_a_blank_line_then_the_reply():
    handled, *_ = go("pause trading", reply="It starts with an action word.")
    assert handled.replies == [f"{replies.refused_line()}\n\nIt starts with an action word."]


def test_an_injected_link_in_the_refusal_reply_is_removed():
    handled, *_ = go("pause trading", reply="Visit https://evil.example or t.me/x now.")
    assert handled.replies[0].endswith("Visit [link removed] or [link removed] now.")


@pytest.mark.parametrize(
    "answer",
    [
        FakeModel(error=ModelUnavailable("x")),
        FakeModel("not json"),
        FakeModel({"reply": " "}),
        FakeModel({"nope": 1}),
    ],
)
def test_a_failed_or_invalid_refusal_call_sends_the_keyword_fallback(answer):
    handled, store, *_ = go("pause trading", answer=answer)
    assert handled.replies == [
        replies.refused_fallback("pause", CONFIG.action_words, replies.CONTROLS)
    ]
    assert handled.outcome.detail == "fallback" and store.known_calls == 0


def test_a_long_message_is_cut_in_the_document():
    _, _, _, answer_model, _ = go("sell " + "x" * 4000)
    assert len(json.loads(answer_model.calls[0]["user"])["message"]) == 1500


# --- the model flag (ADR 0025 §2) -----------------------------------------------------


def test_the_flag_refuses_before_any_store_call_even_with_queries():
    handled, store, plan_model, answer_model, memory = go(
        "please pause trading", plan=flagged(PLAN_Q)
    )
    assert store.known_calls == 0 and store.run_calls == []
    assert len(plan_model.calls) == 1 and len(answer_model.calls) == 1
    doc = json.loads(answer_model.calls[0]["user"])
    assert set(doc) == {"message", "trigger", "matched_word", "action_words", "controls"}
    assert doc["trigger"] == "model_flag" and doc["matched_word"] == ""
    assert answer_model.calls[0]["system"] == REFUSAL_SYSTEM
    assert handled.replies[0].startswith(replies.refused_line() + "\n\n")
    assert handled.outcome.trigger == "model_flag"
    assert memory.recent(NOW) == []


def test_the_flag_fallback_is_used_when_the_refusal_call_fails():
    handled, store, *_ = go(
        "please pause trading", plan=flagged(PLAN_Q), answer=FakeModel(error=ModelUnavailable("x"))
    )
    assert handled.replies == [replies.refused_flag_fallback(CONFIG.action_words, replies.CONTROLS)]
    assert store.known_calls == 0


def test_the_keyword_is_refused_before_the_plan_model_whatever_it_would_say():
    handled, _, plan_model, *_ = go("pause trading", plan={"action_request": False, "queries": []})
    assert plan_model.calls == [] and handled.outcome.trigger == "keyword"


@pytest.mark.parametrize("text", ["why did the PM sell MU?", "Stop-loss triggers this week?"])
def test_a_question_with_the_flag_off_takes_the_question_flow(text):
    handled, _, plan_model, answer_model, memory = go(
        text, plan={"action_request": False, "queries": []}, reply="Here is what happened."
    )
    assert handled.outcome.kind == "answered" and handled.replies == ["Here is what happened."]
    assert len(plan_model.calls) == 1
    assert json.loads(answer_model.calls[0]["user"]).get("question") == text
    assert len(memory.recent(NOW)) == 1


def test_a_keyword_refusal_logs_the_word_trigger_and_tokens(caplog):
    with caplog.at_level(logging.INFO, logger=LOG):
        go("pause trading")
    assert [r.getMessage() for r in caplog.records] == [
        "assistant: refused prompt_version=0.3 trigger=keyword word=pause "
        "tokens=1200/300 outcome=explained"
    ]


def test_a_model_flag_refusal_logs_both_calls_tokens(caplog):
    with caplog.at_level(logging.INFO, logger=LOG):
        go("please pause trading", plan=flagged())
    assert [r.getMessage() for r in caplog.records] == [
        "assistant: refused prompt_version=0.3 trigger=model_flag "
        "tokens=1200/300,1200/300 outcome=explained"
    ]


def test_a_failed_flag_refusal_logs_a_dash_for_the_missing_call(caplog):
    with caplog.at_level(logging.INFO, logger=LOG):
        go("please pause trading", plan=flagged(), answer=FakeModel(error=ModelUnavailable("x")))
    assert "tokens=1200/300,- outcome=fallback" in caplog.text


def test_the_log_line_never_holds_the_message(caplog):
    with caplog.at_level(logging.DEBUG, logger=LOG):
        go("pause MARKER-MSG")
    assert "MARKER-MSG" not in caplog.text
