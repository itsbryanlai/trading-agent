"""`python -m trading_agent.portfolio_manager`, User Story 1: a happy run with fakes
injected, exit 3 when the database is unreachable, and the startup refusals
(specs/008-portfolio-manager contracts/pm-interface.md; research P9)."""

from __future__ import annotations

import logging

import psycopg
import pytest

from tests.fakes.model import FakeModel
from tests.fakes.pm_store import FakePmStore
from tests.unit.portfolio_manager.builders import inputs, report
from tests.unit.portfolio_manager.conftest import FAKE_DASHSCOPE, FAKE_FINNHUB, FAKE_URL, SECRETS
from tests.unit.portfolio_manager.service_support import Clock, answer_of, decide, market
from trading_agent.portfolio_manager import __main__ as runner


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def run(args=(), *, store=None, connect_error=None, answer=None, accounts=True):
    seen: dict = {}
    conn = FakeConn()
    clock = Clock()
    store = store or FakePmStore(inputs([report("db-aapl", "AAPL")], account=accounts))
    model = FakeModel(answer if answer is not None else answer_of(decide()))

    def connect(url, **kw):
        seen["connect"] = (url, kw)
        if connect_error is not None:
            raise connect_error
        return conn

    def store_factory(c):
        seen["store_conn"] = c
        return store

    def quotes_factory(key):
        seen["quote_key"] = key
        return market(("AAPL", "200"))

    def model_factory(provider, key, settings, *, base_url=None):
        seen["model"] = (provider, key, settings.name, base_url)
        return model

    code = runner.main(
        list(args),
        quotes_factory=quotes_factory,
        model_factory=model_factory,
        connect=connect,
        store_factory=store_factory,
        clock=clock,
        sleep=clock.sleep,
    )
    return code, store, conn, seen


def test_a_happy_run_writes_and_exits_zero(env):
    code, store, conn, seen = run()
    assert code == 0
    assert [d.symbol for d in store.written] == ["AAPL"]
    assert conn.closed
    assert seen["connect"][0] == FAKE_URL
    assert seen["connect"][1]["autocommit"] is True
    assert seen["connect"][1]["connect_timeout"] == 10
    assert seen["store_conn"] is conn
    assert seen["quote_key"] == FAKE_FINNHUB
    assert seen["model"][:3] == ("qwen", FAKE_DASHSCOPE, "qwen3.7-plus")
    assert seen["model"][3] == "https://qwen.example.test/compatible-mode/v1"


def test_an_unreachable_database_exits_three_and_names_no_value(env, caplog):
    code, store, _, _ = run(connect_error=psycopg.OperationalError(f"could not connect {FAKE_URL}"))
    assert code == 3
    assert "database unreachable: OperationalError" in caplog.text
    assert not any(secret in caplog.text for secret in SECRETS)


def test_a_failed_read_exits_three_and_closes_the_connection(env):
    data = inputs([report("a", "AAPL")])
    code, _, conn, _ = run(store=FakePmStore(data, fail_read=True))
    assert code == 3
    assert conn.closed


def test_a_failed_write_exits_three(env):
    data = inputs([report("a", "AAPL")])
    code, store, conn, _ = run(store=FakePmStore(data, fail_write=True))
    assert (code, store.writes, conn.closed) == (3, [], True)


def test_a_failure_the_service_reports_exits_one(env):
    code, store, _, _ = run(accounts=False)
    assert (code, store.writes) == (1, [])


def test_a_quiet_run_exits_zero(env):
    code, store, _, _ = run(answer=answer_of())
    assert (code, store.writes) == (0, [])


def test_any_argument_is_refused(env):
    code, store, _, seen = run(["--bogus"])
    assert code == 2
    assert "connect" not in seen


@pytest.mark.parametrize(
    "variable",
    [
        "PORTFOLIO_MANAGER_DATABASE_URL",
        "PORTFOLIO_MANAGER_FINNHUB_API_KEY",
        "PORTFOLIO_MANAGER_DASHSCOPE_API_KEY",
        "PORTFOLIO_MANAGER_QWEN_BASE_URL",
    ],
)
def test_a_missing_variable_is_named_and_refuses_to_start(env, monkeypatch, caplog, variable):
    monkeypatch.delenv(variable)
    code, _, _, seen = run()
    assert code == 2
    assert variable in caplog.text
    assert "connect" not in seen
    assert not any(secret in caplog.text for secret in SECRETS)


@pytest.mark.parametrize(
    "value", ["http://qwen.example.test", "qwen.example.test", " https://x.test"]
)
def test_the_qwen_endpoint_must_be_https_and_is_never_echoed(env, monkeypatch, caplog, value):
    monkeypatch.setenv("PORTFOLIO_MANAGER_QWEN_BASE_URL", value)
    code, _, _, seen = run()
    assert code == 2
    assert (
        "refusing to start: PORTFOLIO_MANAGER_QWEN_BASE_URL must be an https:// URL" in caplog.text
    )
    assert value.strip() not in caplog.text.replace("PORTFOLIO_MANAGER_QWEN_BASE_URL", "")
    assert "connect" not in seen


def test_a_bad_config_file_refuses_to_start(env, tmp_path):
    bad = tmp_path / "pm.yaml"
    bad.write_text("quote_max_age_minutes: 5\n")
    code = runner.main([], config_path=bad, connect=lambda *a, **k: pytest.fail("connected"))
    assert code == 2


def test_the_pm_never_logs_a_variables_value(env, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.portfolio_manager")
    run()
    assert not any(secret in caplog.text for secret in SECRETS)


def test_the_qwen_client_is_built_with_the_pms_schema_name_and_endpoint():
    from trading_agent.llm.settings import ModelSettings

    settings = ModelSettings("qwen", "qwen3.7-plus", 8000, 150, "medium")
    client = runner.build_model("qwen", "k", settings, base_url="https://q.example.test/v1")
    assert client._schema_name == "pm_answer"
    assert client._url == "https://q.example.test/v1/chat/completions"


def test_the_anthropic_client_is_built_from_the_key_and_settings(monkeypatch):
    from trading_agent.llm.settings import ModelSettings

    seen = {}

    class FakeSDK:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr("trading_agent.llm.anthropic_client.anthropic.Anthropic", FakeSDK)
    settings = ModelSettings("anthropic", "claude-sonnet-5-5", 8000, 150, "medium")
    client = runner.build_model("anthropic", "k", settings)
    assert (seen["api_key"], seen["timeout"]) == ("k", 150)
    assert "claude-sonnet-5-5" in repr(client)
