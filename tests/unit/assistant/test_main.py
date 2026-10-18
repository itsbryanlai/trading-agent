"""`python -m trading_agent.assistant`: start-up checks, exit codes and the wiring
(specs/013 tasks T014; contracts/assistant-interface.md; research A18). Every value is a fake."""

from __future__ import annotations

import logging
import signal

import pytest

from tests.fakes.assistant_store import FakeStore, failing
from tests.fakes.model import FakeModel
from tests.fakes.telegram import FakeTelegram
from trading_agent.assistant import __main__ as runner
from trading_agent.assistant.telegram import TokenRejected

FAKE_DB = "postgresql://ta_assistant_login:fake-db-not-real@localhost/none"
FAKE_TOKEN = "000000:fake-not-real"
FAKE_DASH = "fake-dashscope-not-real"
FAKE_ANTHROPIC = "fake-anthropic-not-real"
FAKE_URL = "https://qwen.example.test/compatible-mode/v1"
SECRETS = (FAKE_DB, FAKE_TOKEN, FAKE_DASH, FAKE_ANTHROPIC, "fake-not-real", "4242")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ASSISTANT_DATABASE_URL", FAKE_DB)
    monkeypatch.setenv("ASSISTANT_TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("ASSISTANT_TELEGRAM_CHAT_ID", "4242")
    monkeypatch.setenv("ASSISTANT_DASHSCOPE_API_KEY", FAKE_DASH)
    monkeypatch.setenv("ASSISTANT_QWEN_BASE_URL", FAKE_URL)
    monkeypatch.delenv("ASSISTANT_ANTHROPIC_API_KEY", raising=False)


def question(update_id=1, text="what do we hold?"):
    return {"update_id": update_id, "message": {"message_id": 1, "text": text,
            "chat": {"id": 4242, "type": "private"}}}


class Telegram(FakeTelegram):
    """Delivers SIGTERM, through the installed handler, once a long poll finds nothing."""

    def get_updates(self, offset, timeout, allowed_updates, limit):
        got = super().get_updates(offset, timeout, allowed_updates, limit)
        if timeout > 0 and not got:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return got


def run(args=(), *, updates=(), store=None, plan=None, answer=None, telegram=None,
        config_path=None):
    seen: dict = {"models": []}
    telegram = telegram or Telegram(list(updates))
    store = store if store is not None else FakeStore(rows={"portfolio_now": [{"x": 1}]})
    plan_model = plan or FakeModel({"action_request": False, "queries": [
        {"name": "portfolio_now", "symbol": "", "agent": "", "from_date": "", "to_date": "",
         "section": ""}]})
    answer_model = answer or FakeModel({"reply": "You hold MU."})

    def telegram_factory(token, *, timeout):
        seen["telegram"] = (token, timeout)
        return telegram

    def model_factory(provider, key, settings, *, base_url=None, schema_name):
        seen["models"].append((provider, key, base_url, schema_name))
        return plan_model if schema_name.endswith("plan") or provider == "anthropic" \
            else answer_model

    def store_factory(url, config):
        seen["store"] = url
        return store

    kwargs = {"config_path": config_path} if config_path is not None else {}
    code = runner.main(list(args), telegram_factory=telegram_factory,
                       model_factory=model_factory, store_factory=store_factory,
                       sleep=lambda s: None, **kwargs)
    return code, telegram, store, seen, plan_model, answer_model


def test_a_run_answers_a_question_and_stops_on_sigterm_with_exit_zero(env, caplog):
    with caplog.at_level(logging.INFO, logger="trading_agent.assistant"):
        code, telegram, store, seen, *_ = run(updates=[question()])
    # the waiting message was skipped by the drain, not answered
    assert code == 0 and telegram.sent[0][1].startswith("I was offline and skipped 1")
    assert "assistant: start prompt_version=0.1 provider=qwen model=qwen3.7-plus" in caplog.text
    assert "assistant: stopping (SIGTERM)" in caplog.text
    assert seen["telegram"][0] == FAKE_TOKEN and seen["store"] == FAKE_DB


def test_a_question_that_arrives_after_the_start_is_answered(env):
    telegram = _late_question()
    code, _, store, *_ = run(telegram=telegram)
    assert code == 0
    assert telegram.sent == [(4242, "You hold MU.")]
    assert [c.name for c in store.run_calls[0]] == ["portfolio_now"]


def test_qwen_builds_two_clients_with_their_schema_names_and_the_endpoint(env):
    *_, seen, _, _ = run()
    assert seen["models"] == [
        ("qwen", FAKE_DASH, FAKE_URL, "assistant_plan"),
        ("qwen", FAKE_DASH, FAKE_URL, "assistant_answer"),
    ]


def test_anthropic_builds_one_client_for_both_calls(env, monkeypatch, tmp_path):
    text = runner.DEFAULT_CONFIG_PATH.read_text().replace("provider: qwen", "provider: anthropic")
    text = text.replace("name: qwen3.7-plus", "name: claude-sonnet-5-5")
    path = tmp_path / "assistant.yaml"
    path.write_text(text)
    monkeypatch.delenv("ASSISTANT_DASHSCOPE_API_KEY")
    monkeypatch.delenv("ASSISTANT_QWEN_BASE_URL")
    monkeypatch.setenv("ASSISTANT_ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    code, *_, seen, _, _ = run(config_path=path)
    assert code == 0
    assert seen["models"] == [("anthropic", FAKE_ANTHROPIC, None, "assistant")]


@pytest.mark.parametrize(
    "variable",
    ["ASSISTANT_DATABASE_URL", "ASSISTANT_TELEGRAM_BOT_TOKEN", "ASSISTANT_TELEGRAM_CHAT_ID",
     "ASSISTANT_DASHSCOPE_API_KEY", "ASSISTANT_QWEN_BASE_URL"],
)
def test_a_missing_variable_is_named_and_refuses_to_start(env, monkeypatch, caplog, variable):
    monkeypatch.delenv(variable)
    code, telegram, *_ = run()
    assert code == 2 and telegram.sent == []
    assert variable in caplog.text
    assert not any(secret in caplog.text for secret in SECRETS)


@pytest.mark.parametrize("value", ["abc", "12.5", " ", "42 43", "0x10", "4242; drop"])
def test_a_chat_id_that_is_not_an_integer_refuses_to_start(env, monkeypatch, caplog, value):
    monkeypatch.setenv("ASSISTANT_TELEGRAM_CHAT_ID", value)
    code, *_ = run()
    assert code == 2 and "ASSISTANT_TELEGRAM_CHAT_ID" in caplog.text
    assert value.strip() == "" or value not in caplog.text


@pytest.mark.parametrize(
    "value", ["nocolon", "123:", ":abc", "12:ab cd", "abc:def", "123:ab/cd", "123:ab\n"]
)
def test_a_malformed_bot_token_is_named_and_never_echoed(env, monkeypatch, caplog, value):
    monkeypatch.setenv("ASSISTANT_TELEGRAM_BOT_TOKEN", value)
    code, telegram, *_ = run()
    assert code == 2 and telegram.sent == []
    assert "ASSISTANT_TELEGRAM_BOT_TOKEN" in caplog.text
    assert value.strip() not in caplog.text


@pytest.mark.parametrize("value", ["http://qwen.example.test/v1", "qwen.example.test", "ftp://x/y"])
def test_the_qwen_endpoint_must_be_https_and_is_never_echoed(env, monkeypatch, caplog, value):
    monkeypatch.setenv("ASSISTANT_QWEN_BASE_URL", value)
    code, *_ = run()
    assert code == 2 and "ASSISTANT_QWEN_BASE_URL must be an https:// URL" in caplog.text
    assert value not in caplog.text


@pytest.mark.parametrize("args", [["--bogus"], ["extra"], ["--check", "--bogus"]])
def test_unknown_arguments_exit_two(env, args, caplog):
    code, telegram, *_ = run(args)
    assert code == 2 and telegram.sent == [] and telegram.offsets == []


def test_a_bad_config_file_refuses_to_start(env, tmp_path):
    path = tmp_path / "assistant.yaml"
    path.write_text("owner_time_zone: Nowhere/Land\n")
    code, telegram, *_ = run(config_path=path)
    assert code == 2 and telegram.offsets == []


def test_a_rejected_token_at_start_exits_two_and_names_the_variable(env, caplog):
    telegram = Telegram()
    telegram.fail_updates = [TokenRejected("telegram: HTTP 401")]
    code, *_ = run(telegram=telegram)
    assert code == 2 and "ASSISTANT_TELEGRAM_BOT_TOKEN" in caplog.text
    assert not any(secret in caplog.text for secret in SECRETS)


def test_the_database_failing_repeatedly_exits_three(env, caplog):
    telegram = _late_question(3)
    code, *_ = run(telegram=telegram, store=failing())
    assert code == 3
    assert len(telegram.sent) == 3  # each failed question still got its reply
    assert "(consecutive 3/3)" in caplog.text


def test_a_crash_exits_four_and_logs_the_type_only(env, caplog):
    code, *_ = run(plan=FakeModel(error=ValueError("MARKER-SECRET")), telegram=_late_question())
    assert code == 4
    assert "ValueError" in caplog.text and "MARKER-SECRET" not in caplog.text


def _late_question(count=1):
    class Late(Telegram):
        def get_updates(self, offset, timeout, allowed_updates, limit):
            if timeout > 0 and not self.late:
                self.late = True
                for n in range(1, count + 1):
                    self.push(question(n))
            return super().get_updates(offset, timeout, allowed_updates, limit)

    telegram = Late()
    telegram.late = False
    return telegram


def test_the_sigterm_handler_is_restored_after_the_run(env):
    before = signal.getsignal(signal.SIGTERM)
    run()
    assert signal.getsignal(signal.SIGTERM) is before


def test_no_variable_value_is_ever_logged(env, caplog):
    with caplog.at_level(logging.DEBUG):
        run(updates=[question()])
    assert not any(secret in caplog.text for secret in SECRETS)


def test_main_drains_answers_one_question_and_stops_on_the_stop_flag(env):
    """T028: every factory is a fake; the offline message is skipped, the live one answered."""
    telegram = _late_question()
    telegram.push(question(0, "asked while offline"))
    code, _, store, seen, plan_model, answer_model = run(telegram=telegram)
    assert code == 0
    assert telegram.sent[0][1].startswith("I was offline and skipped 1")
    assert telegram.sent[1:] == [(4242, "You hold MU.")]
    assert len(plan_model.calls) == 1 and len(answer_model.calls) == 1
    assert [c.name for c in store.run_calls[0]] == ["portfolio_now"]


def test_the_production_factories_are_the_real_clients():
    import inspect

    from trading_agent.assistant.store import PostgresStore
    from trading_agent.assistant.telegram import TelegramClient

    defaults = inspect.signature(runner.main).parameters
    assert defaults["telegram_factory"].default is TelegramClient
    assert defaults["store_factory"].default is runner.build_store
    assert defaults["model_factory"].default is runner.build_model
    from trading_agent.assistant.config import load_config

    store = runner.build_store(FAKE_DB, load_config())
    assert isinstance(store, PostgresStore) and FAKE_DB not in repr(store)  # no connection made


def test_a_settings_question_reads_the_config_directory_beside_the_config_file(
    env, tmp_path
):
    for name in ("assistant.yaml",):
        (tmp_path / name).write_text((runner.DEFAULT_CONFIG_PATH.parent / name).read_text())
    (tmp_path / "risk.yaml").write_text("max_position_pct: 7\nuniverse: {}\n")
    plan = FakeModel({"action_request": False, "queries": [
        {"name": "settings", "symbol": "", "agent": "", "from_date": "", "to_date": "",
         "section": "risk"}]})
    code, _, _, _, _, answer_model = run(
        telegram=_late_question(), plan=plan, config_path=tmp_path / "assistant.yaml")
    assert code == 0
    sent = answer_model.calls[0]["user"]
    assert '"max_position_pct":7' in sent
