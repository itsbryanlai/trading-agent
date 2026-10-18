"""The log guard (specs/013 tasks T022; SC-004, FR-021, FR-015): no log record holds a
message, a reply, a stored row, a variable's value or the chat ID, and no model call sees a
variable's value or the chat ID."""

from __future__ import annotations

import json
import logging
import signal

import pytest

from tests.fakes.assistant_store import FakeStore
from tests.fakes.model import FakeModel
from tests.fakes.telegram import FakeTelegram
from trading_agent.assistant import __main__ as runner
from trading_agent.assistant.store import StoreError
from trading_agent.assistant.telegram import TelegramUnavailable
from trading_agent.llm.ports import ModelUnavailable

CHAT = 987654321
STRANGER = 55555
MARKERS = ("MARKER-MSG", "MARKER-ROW", "MARKER-REPLY", "MARKER-SECRET", str(CHAT))
LEAK = "https://api.telegram.org/botMARKER-SECRET/getUpdates"
QUERY = {"name": "portfolio_now", "symbol": "", "agent": "", "from_date": "", "to_date": "",
         "section": ""}


def update(n, text, chat=CHAT):
    return {"update_id": n, "message": {"message_id": n, "text": text,
            "chat": {"id": chat, "type": "private"}}}


class Scripted(FakeModel):
    """Answers from a list, one per call; an exception in the list is raised."""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)

    def complete(self, system, user, schema):
        item = self.script.pop(0)
        self.error = item if isinstance(item, Exception) else None
        if not isinstance(item, Exception):
            self.text = json.dumps(item)
        return super().complete(system, user, schema)


class Store(FakeStore):
    def __init__(self, fail_on_call):
        super().__init__(rows={"portfolio_now": [{"note": "MARKER-ROW"}]})
        self.fail_on_call = fail_on_call

    def known_symbols(self):
        self.known_calls += 1
        if self.known_calls == self.fail_on_call:
            raise StoreError("database: MARKER-SECRET")
        return set(self.known)


class Telegram(FakeTelegram):
    def __init__(self, waiting, late):
        super().__init__(waiting)
        self.late = list(late)

    def get_updates(self, offset, timeout, allowed_updates, limit):
        if timeout > 0 and self.late:
            for item in self.late:
                self.push(item)
            self.late = []
        got = super().get_updates(offset, timeout, allowed_updates, limit)
        if timeout > 0 and not got:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return got


@pytest.fixture
def scenario(monkeypatch, caplog):
    monkeypatch.setenv("ASSISTANT_DATABASE_URL", "postgresql://u:MARKER-SECRET@h/d")
    monkeypatch.setenv("ASSISTANT_TELEGRAM_BOT_TOKEN", "123456:MARKER-SECRET")
    monkeypatch.setenv("ASSISTANT_TELEGRAM_CHAT_ID", str(CHAT))
    monkeypatch.setenv("ASSISTANT_DASHSCOPE_API_KEY", "MARKER-SECRET-key")
    monkeypatch.setenv("ASSISTANT_QWEN_BASE_URL", "https://MARKER-SECRET.example.test/v1")
    waiting = [update(1, "MARKER-MSG waiting")]
    late = [
        update(2, "MARKER-MSG what do we hold?"),               # answered
        update(3, "pause MARKER-MSG"),                           # keyword refusal
        update(4, "please MARKER-MSG halt everything"),          # model-flag refusal
        update(5, "/help MARKER-MSG"),                           # fixed reply
        update(6, "MARKER-MSG a question the model fails on"),   # model failure
        update(7, "MARKER-MSG a question the database fails on"),
        update(8, "MARKER-MSG from a stranger", chat=STRANGER),  # ignored chat
    ]
    telegram = Telegram(waiting, late)
    telegram.fail_updates = [TelegramUnavailable(LEAK)]
    telegram.fail_send = [TelegramUnavailable(LEAK)]
    plan = {"action_request": False, "queries": [QUERY]}
    flagged = {"action_request": True, "queries": [QUERY]}
    plan_model = Scripted([plan, flagged, plan, plan])
    answer_model = Scripted([
        {"reply": "MARKER-REPLY answer"}, {"reply": "MARKER-REPLY keyword"},
        {"reply": "MARKER-REPLY flag"}, ModelUnavailable("MARKER-SECRET detail"),
    ])
    store = Store(fail_on_call=3)  # known_symbols runs for messages 2, 6 and 7 only
    with caplog.at_level(logging.DEBUG):
        code = runner.main(
            [], telegram_factory=lambda token, *, timeout: telegram,
            model_factory=lambda provider, key, settings, *, base_url=None, schema_name:
                plan_model if schema_name.endswith("plan") else answer_model,
            store_factory=lambda url, config: store, sleep=lambda s: None,
        )
    return code, telegram, plan_model, answer_model, caplog.text


def test_the_run_finishes_cleanly(scenario):
    code, telegram, *_ = scenario
    assert code == 0 and len(telegram.sent) >= 7


@pytest.mark.parametrize("marker", MARKERS)
def test_no_log_record_holds_a_marker(scenario, marker):
    assert marker not in scenario[-1]


@pytest.mark.parametrize("marker", ["MARKER-SECRET", str(CHAT)])
def test_no_model_call_sees_a_secret_or_the_chat_id(scenario, marker):
    _, _, plan_model, answer_model, _ = scenario
    for call in plan_model.calls + answer_model.calls:
        assert marker not in call["system"] and marker not in call["user"]


@pytest.mark.parametrize(
    "shape",
    [
        "assistant: start prompt_version=0.2 provider=qwen model=qwen3.7-plus",
        "assistant: skipped 1 waiting message(s)",
        "assistant: telegram getUpdates failed: TelegramUnavailable, retry in 1s",
        "assistant: telegram sendMessage failed: TelegramUnavailable, retry in 2s",
        "assistant: answered prompt_version=0.2 queries=portfolio_now dropped=none cut=none "
        "tokens=1200/300,1200/300 links_removed=0 messages=1 outcome=answered",
        "assistant: refused prompt_version=0.2 trigger=keyword word=pause tokens=1200/300 "
        "outcome=explained",
        "assistant: refused prompt_version=0.2 trigger=model_flag tokens=1200/300,1200/300 "
        "outcome=explained",
        "assistant: replied fixed=R-HELP",
        "outcome=model_failed:unavailable",
        "outcome=database",
        "assistant: database failed: StoreError (consecutive 1/3)",
        "assistant: ignored 1 update(s) from other chats",
        "assistant: stopping (SIGTERM)",
    ],
)
def test_the_documented_line_shapes_appear(scenario, shape):
    assert shape in scenario[-1]
