"""`--check` and `--find-chat-id` (specs/013 T023; research A18). Every value is a fake."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes.assistant_store import FakeStore, failing
from tests.fakes.model import FakeModel
from tests.fakes.telegram import FakeTelegram
from trading_agent.assistant import __main__ as runner
from trading_agent.assistant import check
from trading_agent.assistant.answer import PLAN_SCHEMA, parse_plan
from trading_agent.assistant.config import DEFAULT_CONFIG_PATH
from trading_agent.assistant.telegram import TelegramUnavailable, TokenRejected
from trading_agent.llm.ports import ModelUnavailable

NOW = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)
GOOD_PLAN = {"action_request": False, "queries": []}
GOOD_ANSWER = {"reply": "Hello."}
CONFIG_DIR = DEFAULT_CONFIG_PATH.parent


def go(
    *,
    telegram=None,
    store=None,
    model=None,
    answer=None,
    zone="Asia/Singapore",
    config_dir=CONFIG_DIR,
):
    lines: list[str] = []
    code = check.run_checks(
        telegram or FakeTelegram(),
        store or FakeStore(),
        model or FakeModel(GOOD_PLAN),
        answer or FakeModel(GOOD_ANSWER),
        zone_name=zone,
        config_dir=config_dir,
        now=NOW,
        out=lines.append,
    )
    return code, lines


def test_a_working_setup_passes_every_check_and_sends_nothing():
    telegram = FakeTelegram()
    code, lines = go(telegram=telegram)
    assert code == 0
    assert lines == [
        "telegram: ok",
        "telegram_webhook: ok",
        "database: ok",
        "model_plan: ok",
        "model_answer: ok",
        "time_zone: ok",
        "settings: ok",
    ]
    assert telegram.sent == [] and telegram.offsets == []


def test_the_model_check_uses_the_plan_schema_and_a_greeting():
    model = FakeModel(GOOD_PLAN)
    go(model=model)
    (call,) = model.calls
    assert call["schema"] == PLAN_SCHEMA and "hello" in call["user"]
    assert parse_plan(model.text) is not None


class RejectedTelegram(FakeTelegram):
    def get_me(self):
        raise TokenRejected("x")


@pytest.mark.parametrize(
    ("kwargs", "name", "kind"),
    [
        ({"telegram": RejectedTelegram()}, "telegram", "TokenRejected"),
        ({"store": failing()}, "database", "StoreError"),
        (
            {"model": FakeModel(error=ModelUnavailable("MARKER-SECRET"))},
            "model_plan",
            "ModelUnavailable",
        ),
        (
            {"answer": FakeModel(error=ModelUnavailable("MARKER-SECRET"))},
            "model_answer",
            "ModelUnavailable",
        ),
        ({"zone": "Nowhere/Land"}, "time_zone", "ZoneInfoNotFoundError"),
    ],
)
def test_a_failure_names_the_check_and_the_exception_type_only(kwargs, name, kind):
    code, lines = go(**kwargs)
    assert code == 1
    failed = [line for line in lines if line.startswith(f"{name}:")]
    assert failed == [f"{name}: FAILED {kind}"]
    assert "MARKER-SECRET" not in "\n".join(lines)
    assert len(lines) == 7  # the other checks still run


def test_a_model_answer_that_is_not_a_plan_fails():
    code, lines = go(model=FakeModel("not json"))
    assert code == 1 and "model_plan: FAILED UnusablePlan" in lines


def test_an_unparseable_answer_fails_model_answer_only():
    code, lines = go(answer=FakeModel("not json"))
    assert code == 1 and "model_answer: FAILED UnusableAnswer" in lines
    assert "model_plan: ok" in lines


def test_the_answer_check_uses_the_answer_schema():
    from trading_agent.assistant.answer import ANSWER_SCHEMA

    answer = FakeModel(GOOD_ANSWER)
    go(answer=answer)
    assert answer.calls[0]["schema"] == ANSWER_SCHEMA


def test_missing_settings_files_fail_the_settings_check(tmp_path):
    code, lines = go(config_dir=tmp_path)
    assert code == 1 and "settings: FAILED Unavailable" in lines


def update(update_id, chat_id=-100, kind="group", text="hi"):
    return {
        "update_id": update_id,
        "message": {"message_id": 1, "text": text, "chat": {"id": chat_id, "type": kind}},
    }


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def find(telegram, *, step=30.0):
    clock, lines = Clock(), []
    original = telegram.get_updates

    def advancing(offset, timeout, allowed_updates, limit):
        clock.t += step
        return original(offset, timeout, allowed_updates, limit)

    telegram.get_updates = advancing
    code = check.find_chat_id(telegram, clock=clock, out=lines.append)
    return code, lines, clock


def test_find_chat_id_prints_each_chat_once_and_sends_nothing():
    telegram = FakeTelegram([update(5, 4242, "private"), update(6, -7, "group")])
    code, lines, clock = find(telegram)
    assert code == 0
    assert lines == ["chat.id=4242 chat.type=private", "chat.id=-7 chat.type=group"]
    assert telegram.sent == []
    assert telegram.offsets[0] == 0 and 7 in telegram.offsets  # offset advanced past the last


def test_find_chat_id_stops_after_120_seconds_in_bounded_polls():
    telegram = FakeTelegram()
    code, lines, clock = find(telegram, step=30.0)
    assert code == 0 and lines == [] and clock.t >= 120
    assert len(telegram.offsets) == 4


def test_find_chat_id_skips_updates_without_a_message():
    telegram = FakeTelegram([{"update_id": 1, "edited_message": {}}, update(2, 9, "private")])
    _, lines, _ = find(telegram)
    assert lines == ["chat.id=9 chat.type=private"]


def test_find_chat_id_reports_a_rejected_token_with_exit_two():
    telegram = FakeTelegram()
    telegram.fail_updates = [TokenRejected("x")]
    code, lines, _ = find(telegram)
    assert code == 2 and lines == ["telegram: FAILED TokenRejected"]


def test_find_chat_id_reports_other_failures_with_exit_one():
    telegram = FakeTelegram()
    telegram.fail_updates = [TelegramUnavailable("telegram: URLError")]
    code, lines, _ = find(telegram)
    assert code == 1 and lines == ["telegram: FAILED TelegramUnavailable"]


# ---- through main() -------------------------------------------------------------------------

FAKE_TOKEN = "000000:fake-not-real"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ASSISTANT_DATABASE_URL", "postgresql://u:fake-db-not-real@localhost/none")
    monkeypatch.setenv("ASSISTANT_TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("ASSISTANT_TELEGRAM_CHAT_ID", "4242")
    monkeypatch.setenv("ASSISTANT_DASHSCOPE_API_KEY", "fake-dashscope-not-real")
    monkeypatch.setenv("ASSISTANT_QWEN_BASE_URL", "https://qwen.example.test/v1")
    monkeypatch.delenv("ASSISTANT_ANTHROPIC_API_KEY", raising=False)


def main(args, telegram, **env_overrides):
    lines: list[str] = []
    code = runner.main(
        args,
        telegram_factory=lambda token, *, timeout: telegram,
        model_factory=lambda *a, schema_name, **k: FakeModel(
            GOOD_ANSWER if schema_name.endswith("answer") else GOOD_PLAN
        ),
        store_factory=lambda url, cfg: FakeStore(),
        sleep=lambda s: None,
        out=lines.append,
    )
    return code, lines


def test_main_check_runs_the_checks_and_exits_zero(env):
    telegram = FakeTelegram()
    code, lines = main(["--check"], telegram)
    assert code == 0 and len(lines) == 7 and all(line.endswith("ok") for line in lines)
    assert telegram.sent == []


def test_main_check_exits_one_on_a_failure(env):
    class Bad(FakeTelegram):
        def get_me(self):
            raise TokenRejected("x")

    code, lines = main(["--check"], Bad())
    assert code == 1 and "telegram: FAILED TokenRejected" in lines


def test_main_check_without_a_variable_exits_two(env, monkeypatch):
    monkeypatch.delenv("ASSISTANT_DATABASE_URL")
    code, lines = main(["--check"], FakeTelegram())
    assert code == 2 and lines == []


def test_main_find_chat_id_needs_only_the_token(env, monkeypatch):
    for name in (
        "ASSISTANT_DATABASE_URL",
        "ASSISTANT_TELEGRAM_CHAT_ID",
        "ASSISTANT_DASHSCOPE_API_KEY",
        "ASSISTANT_QWEN_BASE_URL",
    ):
        monkeypatch.delenv(name)
    telegram = FakeTelegram([update(1, 4242, "private")])
    monkeypatch.setattr(check, "FIND_SECONDS", 0.05)
    code, lines = main(["--find-chat-id"], telegram)
    assert code == 0 and lines == ["chat.id=4242 chat.type=private"]
    assert FAKE_TOKEN not in "\n".join(lines)


def test_main_find_chat_id_refuses_a_malformed_token(env, monkeypatch):
    monkeypatch.setenv("ASSISTANT_TELEGRAM_BOT_TOKEN", "nocolon")
    code, lines = main(["--find-chat-id"], FakeTelegram())
    assert code == 2 and lines == []


@pytest.mark.parametrize("args", [["--check", "--find-chat-id"], ["--check", "x"], ["--nope"]])
def test_other_argument_combinations_exit_two(env, args):
    code, _ = main(args, FakeTelegram())
    assert code == 2


def test_find_chat_id_without_a_token_logs_one_critical_exiting_line(env, monkeypatch, caplog):
    monkeypatch.delenv("ASSISTANT_TELEGRAM_BOT_TOKEN")
    code, lines = main(["--find-chat-id"], FakeTelegram())
    records = [r for r in caplog.records if r.getMessage().startswith("assistant: exiting:")]
    assert code == 2 and lines == [] and len(records) == 1
    assert records[0].levelno == 50 and "ASSISTANT_TELEGRAM_BOT_TOKEN" in records[0].getMessage()
    assert "refusing" not in caplog.text


def test_a_set_webhook_fails_the_check_without_printing_the_url():
    telegram = FakeTelegram()
    telegram.webhook_url = "https://hook.example/path"
    code, lines = go(telegram=telegram)
    assert code == 1
    assert "telegram_webhook: FAILED webhook_set" in lines
    assert "telegram: ok" in lines
    assert "hook.example" not in "\n".join(lines)


def test_a_failing_webhook_lookup_names_the_exception_type_only():
    class Broken(FakeTelegram):
        def get_webhook_info(self):
            raise TelegramUnavailable("MARKER-SECRET")

    code, lines = go(telegram=Broken())
    assert code == 1
    assert "telegram_webhook: FAILED TelegramUnavailable" in lines
    assert "MARKER-SECRET" not in "\n".join(lines)
