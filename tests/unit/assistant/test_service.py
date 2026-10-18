"""The question flow (specs/013 tasks T012; research A9, A12, A14; analyze H2)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.fakes.assistant_store import FakeStore, failing
from tests.fakes.model import FakeModel
from trading_agent.assistant import replies
from trading_agent.assistant.config import load_config
from trading_agent.assistant.memory import Memory
from trading_agent.assistant.prompt import ANSWER_SYSTEM, PLAN_SYSTEM
from trading_agent.assistant.service import handle
from trading_agent.llm.ports import (
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
)

SGT = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 10, 20, 7, 0, tzinfo=SGT)  # Tue 07:00 +08 = Mon 19:00 EDT
CONFIG = load_config()


def choice(name, **kw):
    base = {"name": name, "symbol": "", "agent": "", "from_date": "", "to_date": "", "section": ""}
    return base | kw


def plan_of(*choices, action=False):
    return {"action_request": action, "queries": list(choices)}


def run(question="what do we hold?", plan=None, reply="You hold MU and AAPL.", *, store=None,
        plan_model=None, answer_model=None, memory=None, config=CONFIG):
    store = store if store is not None else FakeStore(rows={"portfolio_now": [{"x": 1}]})
    if plan is None:
        plan = plan_of(choice("portfolio_now"))
    plan_model = plan_model or FakeModel(plan)
    answer_model = answer_model or FakeModel({"reply": reply})
    memory = memory or Memory(5, 30)
    handled = handle(question, now=NOW, config=config, store=store, plan_model=plan_model,
                     answer_model=answer_model, memory=memory)
    return handled, store, plan_model, answer_model, memory


def answer_doc(answer_model):
    return json.loads(answer_model.calls[0]["user"])


def test_a_question_goes_plan_read_answer_and_the_reply_passes_through():
    handled, store, plan_model, answer_model, _ = run()
    assert handled.replies == ["You hold MU and AAPL."]
    assert handled.outcome.kind == "answered"
    assert plan_model.calls[0]["system"] == PLAN_SYSTEM
    assert answer_model.calls[0]["system"] == ANSWER_SYSTEM
    assert [c.name for c in store.run_calls[0]] == ["portfolio_now"]


def test_two_positions_and_todays_snapshot_reach_the_answer_document():
    rows = [
        {"part": "positions", "symbol": "MU", "qty": "10"},
        {"part": "positions", "symbol": "AAPL", "qty": "5"},
        {"part": "snapshot", "equity": "100500.00", "taken_at": {"owner": "x", "et": "y"}},
    ]
    handled, *_, answer_model, _ = run(store=FakeStore(rows={"portfolio_now": rows}))
    doc = answer_doc(answer_model)
    assert doc["results"][0]["rows"] == rows
    assert doc["results"][0]["query"] == "portfolio_now"
    assert doc["nothing_looked_up"] is False
    assert doc["now"]["local_date"] == "2026-10-20" and doc["now"]["time_zone"] == "Asia/Singapore"


def test_a_paused_system_state_is_passed_on_as_stored():
    rows = [{"part": "system_state", "trading_paused": True}]
    _, *_, answer_model, _ = run(
        "is trading paused?", store=FakeStore(rows={"portfolio_now": rows})
    )
    assert answer_doc(answer_model)["results"][0]["rows"] == rows


def test_three_decisions_with_verdicts_are_passed_on():
    rows = [{"symbol": s, "verdict": v} for s, v in (("MU", "approved"), ("AAPL", "rejected"),
                                                      ("MU", "approved"))]
    _, *_, answer_model, _ = run(
        "what did the PM decide today?",
        plan=plan_of(choice("decisions")),
        store=FakeStore(rows={"decisions": rows}),
    )
    doc = answer_doc(answer_model)
    assert doc["results"][0]["rows"] == rows
    assert doc["results"][0]["parameters"] == {}


def test_a_decisions_price_carries_its_quote_time_in_both_zones_not_generated_at():
    quote_time = {"owner": "2026-10-19T23:59:58+08:00", "et": "2026-10-19T11:59:58-04:00"}
    generated = {"owner": "2026-10-20T02:00:00+08:00", "et": "2026-10-19T14:00:00-04:00"}
    row = {"symbol": "MU", "quote_at_decision": "112.40", "quote_time": quote_time,
           "generated_at": generated}
    _, *_, answer_model, _ = run(
        plan=plan_of(choice("decisions", symbol="MU")),
        store=FakeStore(rows={"decisions": [row]}),
    )
    sent = answer_doc(answer_model)["results"][0]["rows"][0]
    assert sent["quote_time"] == quote_time
    assert set(sent["quote_time"]) == {"owner", "et"}


def test_the_plan_document_holds_the_menu_the_cap_and_the_question():
    _, _, plan_model, *_ = run("what do we hold?")
    doc = json.loads(plan_model.calls[0]["user"])
    assert doc["question"] == "what do we hold?"
    assert doc["max_queries"] == CONFIG.limits.max_queries_per_question
    assert "portfolio_now" in [e["name"] for e in doc["menu"]]


def test_the_plan_and_answer_calls_use_their_schemas():
    from trading_agent.assistant.answer import ANSWER_SCHEMA, PLAN_SCHEMA

    _, _, plan_model, answer_model, _ = run()
    assert plan_model.calls[0]["schema"] == PLAN_SCHEMA
    assert answer_model.calls[0]["schema"] == ANSWER_SCHEMA


def test_an_invalid_plan_looks_nothing_up():
    for bad in ("not json", {"action_request": "no", "queries": []}, {"queries": []}):
        handled, store, _, answer_model, _ = run(plan=bad)
        assert store.run_calls == []
        assert answer_doc(answer_model)["nothing_looked_up"] is True
        assert handled.outcome.kind == "answered"


def test_an_empty_plan_looks_nothing_up_and_skips_the_read():
    handled, store, _, answer_model, _ = run("hello", plan=plan_of())
    assert store.run_calls == []
    assert answer_doc(answer_model)["nothing_looked_up"] is True


def test_invalid_choices_are_dropped_with_a_reason_and_valid_ones_run():
    handled, store, _, answer_model, _ = run(
        plan=plan_of(choice("decisions", symbol="ZZZZ"), choice("portfolio_now"))
    )
    doc = answer_doc(answer_model)
    assert doc["dropped"] == [{"query": "decisions", "reason": "unknown_symbol"}]
    assert [c.name for c in store.run_calls[0]] == ["portfolio_now"]
    assert handled.outcome.dropped == (("decisions", "unknown_symbol"),)


def test_every_choice_dropped_runs_no_query():
    _, store, *_ = run(plan=plan_of(choice("drop_table")))
    assert store.run_calls == []


def test_a_settings_choice_reads_files_and_makes_no_store_run():
    _, store, _, answer_model, _ = run(plan=plan_of(choice("settings", section="risk")))
    assert store.run_calls == []
    doc = answer_doc(answer_model)
    assert doc["dropped"] == [] and doc["nothing_looked_up"] is False
    (result,) = doc["results"]
    assert result["query"] == "settings" and result["parameters"] == {"section": "risk"}
    assert set(result["rows"][0]) == {"risk"}


def test_settings_and_a_database_entry_keep_the_choices_order():
    _, store, _, answer_model, _ = run(
        plan=plan_of(choice("settings"), choice("portfolio_now"))
    )
    assert [c.name for c in store.run_calls[0]] == ["portfolio_now"]
    assert [r["query"] for r in answer_doc(answer_model)["results"]] == [
        "settings", "portfolio_now"
    ]


def test_the_flag_hands_off_before_any_store_call():
    handled, store, plan_model, answer_model, memory = run(
        "please pause trading", plan=plan_of(choice("portfolio_now"), action=True)
    )
    assert store.known_calls == 0 and store.run_calls == []
    assert len(answer_model.calls) == 1  # the refusal call (tests in test_refusal.py)
    assert handled.outcome.kind == "refused" and handled.outcome.trigger == "model_flag"
    assert handled.replies[0].startswith(replies.refused_line())
    assert memory.recent(NOW) == []


def test_the_reply_is_filtered_for_links_but_report_sources_are_kept():
    url = "https://source.example/a"
    rows = [{"id": 1, "sources": [{"title": "t", "url": url}]}]
    reply = f"See {url} and https://evil.example/x or t.me/x and @someone_here."
    store = FakeStore(rows={"reports": rows})
    handled, *_ = run(plan=plan_of(choice("reports")), reply=reply, store=store)
    gone = "[link removed]"
    assert handled.replies == [f"See {url} and {gone} or {gone} and {gone}."]
    assert handled.outcome.links_removed == 3


def test_a_source_url_from_an_earlier_question_is_not_allowed():
    handled, *_ = run(reply="See https://source.example/a now")
    assert handled.replies == ["See [link removed] now"]


def test_the_reply_is_capped_then_split():
    long = "word " * 5000  # 25000 chars
    handled, *_ = run(reply=long)
    assert len(handled.replies) == 4  # 12000 + cut mark, in parts of at most 4000
    assert all(len(r) <= 4000 for r in handled.replies)
    assert handled.replies[-1].endswith(replies.CUT_MARK)
    assert handled.outcome.messages == 4


def test_the_exchange_is_added_to_memory():
    _, *_, memory = run("what do we hold?")
    (exchange,) = memory.recent(NOW)
    assert exchange.question == "what do we hold?" and exchange.reply == "You hold MU and AAPL."
    assert exchange.asked_at == NOW


def test_memory_reaches_the_next_plan_document():
    memory = Memory(5, 30)
    run("first", memory=memory)
    _, _, plan_model, *_ = run("second", memory=memory)
    doc = json.loads(plan_model.calls[0]["user"])
    assert [e["question"] for e in doc["exchanges"]] == ["first"]


def test_an_answer_too_big_returns_too_much_with_no_answer_call():
    store = FakeStore(rows={"portfolio_now": [{"blob": "x" * 70_000}]})
    handled, _, _, answer_model, memory = run(store=store)
    assert answer_model.calls == []
    assert handled.replies == [replies.too_much()]
    assert handled.outcome.kind == "too_much"
    assert memory.recent(NOW) == []


def test_oldest_exchanges_are_dropped_before_too_much():
    memory = Memory(5, 30)
    for n in (1, 2, 3):
        memory.add(NOW, f"q{n}", "r" * 1500)
    store = FakeStore(rows={"portfolio_now": [{"y": "y" * 56_000}]})
    handled, _, _, answer_model, _ = run(store=store, memory=memory)
    kept = [e["question"] for e in answer_doc(answer_model)["exchanges"]]
    assert handled.outcome.kind == "answered"
    assert len(kept) < 3 and kept == ["q1", "q2", "q3"][3 - len(kept):]


@pytest.mark.parametrize(
    "error, code, text",
    [
        (ModelUnavailable("x"), "unavailable", "was unavailable"),
        (ModelRefused("x"), "refused", "refused"),
        (ModelRejected("x"), "rejected", "rejected the request"),
        (ModelKeyRejected("x"), "rejected", "rejected the request"),
        (ModelTruncated("x"), "truncated", "was cut off"),
    ],
)
@pytest.mark.parametrize("which", ["plan", "answer"])
def test_each_model_error_gives_its_category_from_either_call(error, code, text, which):
    kw = {f"{which}_model": FakeModel(error=error)}
    handled, store, _, answer_model, memory = run(**kw)
    assert handled.replies == [replies.model_failed(text)]
    assert handled.outcome.kind == "model_failed" and handled.outcome.category == code
    assert " " not in handled.outcome.category
    assert memory.recent(NOW) == []  # no exchange stored, no partial reply
    if which == "plan":
        assert store.run_calls == [] and answer_model.calls == []


def test_the_failure_codes_and_texts_are_mapped_in_one_place():
    assert set(replies.FAILURE_TEXT.values()) == set(replies.MODEL_FAILURES)
    assert set(replies.FAILURE_TEXT) == {
        "unavailable", "refused", "rejected", "truncated", "unusable"
    }


@pytest.mark.parametrize("bad", ["not json", {"reply": ""}, {"reply": 3}, {"text": "x"}])
def test_an_unusable_answer_gives_the_model_failed_reply(bad):
    handled, *_, memory = run(answer_model=FakeModel(bad))
    assert handled.replies == [replies.model_failed("gave an unusable answer")]
    assert handled.outcome.category == "unusable"
    assert memory.recent(NOW) == []


def test_a_database_failure_gives_the_database_reply():
    handled, _, _, answer_model, memory = run(store=failing())
    assert handled.replies == [replies.database()]
    assert handled.outcome.kind == "database"
    assert handled.outcome.detail == "StoreError"
    assert answer_model.calls == [] and memory.recent(NOW) == []


def test_the_question_line_is_logged_without_text(caplog):
    marker = "MARKER-QUESTION-9c1"
    with caplog.at_level(logging.INFO, logger="trading_agent.assistant"):
        run(marker, reply="MARKER-REPLY-4d2")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "assistant: answered prompt_version=0.1 queries=portfolio_now dropped=none" in text
    assert "tokens=1200/300,1200/300" in text and "outcome=answered" in text
    assert "MARKER" not in text


# --- respond: the minimal dispatch until T018/T019 --------------------------------------


def upd(text=None, **extra):
    message = {"message_id": 1, "chat": {"id": 4242, "type": "private"}, **extra}
    if text is not None:
        message["text"] = text
    return {"update_id": 1, "message": message}


def respond_to(update, store=None):
    from trading_agent.assistant.service import respond

    store = store if store is not None else FakeStore()
    plan_model, answer_model = FakeModel(plan_of()), FakeModel({"reply": "ok"})
    handled = respond(update, now=NOW, config=CONFIG, store=store, plan_model=plan_model,
                      answer_model=answer_model, memory=Memory(5, 30))
    return handled, store, plan_model, answer_model


def test_respond_sends_a_question_through_the_flow():
    handled, *_ = respond_to(upd("what do we hold?"))
    assert handled.replies == ["ok"] and handled.outcome.kind == "answered"


def test_respond_answers_a_command_with_help_and_a_non_text_message_with_text_only():
    handled, store, plan_model, _ = respond_to(upd("/help"))
    assert "can't take any action" in handled.replies[0] and "Asia/Singapore" in handled.replies[0]
    handled, *_ = respond_to(upd(None, photo=[{}]))
    assert handled.replies == [replies.text_only()]
    assert plan_model.calls == [] and store.known_calls == 0


def test_every_fixed_reply_logs_its_id_and_is_not_remembered(caplog):
    memory = Memory(5, 30)
    from trading_agent.assistant.service import respond

    with caplog.at_level(logging.INFO, logger="trading_agent.assistant"):
        for update in (upd("/start"), upd("/whatever"), upd(None, photo=[{}]),
                       upd("x", forward_origin={"type": "user"})):
            respond(update, now=NOW, config=CONFIG, store=FakeStore(),
                    plan_model=FakeModel(plan_of()), answer_model=FakeModel({"reply": "r"}),
                    memory=memory)
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "assistant: replied fixed=R-HELP",
        "assistant: replied fixed=R-HELP",
        "assistant: replied fixed=R-TEXT-ONLY",
        "assistant: replied fixed=R-TEXT-ONLY",
    ]
    assert memory.recent(NOW) == []


def test_a_question_through_respond_is_remembered():
    handled, store, *_ = respond_to(upd("what do we hold?"))
    assert handled.outcome.kind == "answered"
