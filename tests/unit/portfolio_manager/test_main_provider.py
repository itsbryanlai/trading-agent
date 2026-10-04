"""Provider selection (specs/008-portfolio-manager FR-021, contracts/pm-interface.md
"Environment"): the model provider decides which variables are required, and only those."""

from __future__ import annotations

import logging

import pytest
import yaml

from tests.unit.portfolio_manager.conftest import (
    FAKE_ANTHROPIC,
    FAKE_DASHSCOPE,
    FAKE_QWEN_URL,
    SECRETS,
)
from tests.unit.portfolio_manager.main_support import run_main
from trading_agent.portfolio_manager.config import DEFAULT_CONFIG_PATH

ANTHROPIC_KEY = "PORTFOLIO_MANAGER_ANTHROPIC_API_KEY"
DASHSCOPE_KEY = "PORTFOLIO_MANAGER_DASHSCOPE_API_KEY"
QWEN_URL = "PORTFOLIO_MANAGER_QWEN_BASE_URL"


@pytest.fixture
def anthropic_config(tmp_path):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    data["model"].update(provider="anthropic", name="claude-sonnet-5-5")
    path = tmp_path / "pm.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_qwen_needs_only_the_dashscope_key_and_base_url(env, monkeypatch):
    monkeypatch.setenv(ANTHROPIC_KEY, FAKE_ANTHROPIC)
    result = run_main(monkeypatch=monkeypatch)
    assert result.code == 0
    assert result.seen["model"] == ("qwen", FAKE_DASHSCOPE, FAKE_QWEN_URL)
    assert ANTHROPIC_KEY not in result.required


def test_qwen_works_with_no_anthropic_key_set(env, monkeypatch):
    monkeypatch.delenv(ANTHROPIC_KEY, raising=False)
    assert run_main(monkeypatch=monkeypatch).code == 0


def test_anthropic_needs_only_its_own_key(env, monkeypatch, anthropic_config):
    monkeypatch.delenv(DASHSCOPE_KEY)
    monkeypatch.delenv(QWEN_URL)
    monkeypatch.setenv(ANTHROPIC_KEY, FAKE_ANTHROPIC)
    result = run_main(config_path=anthropic_config, monkeypatch=monkeypatch)
    assert result.code == 0
    assert result.seen["model"] == ("anthropic", FAKE_ANTHROPIC, None)
    assert DASHSCOPE_KEY not in result.required and QWEN_URL not in result.required


@pytest.mark.parametrize("variable", [DASHSCOPE_KEY, QWEN_URL])
def test_a_missing_qwen_variable_exits_two_naming_it(env, monkeypatch, caplog, variable):
    monkeypatch.delenv(variable)
    result = run_main(monkeypatch=monkeypatch)
    assert result.code == 2 and "conn" not in result.seen
    assert variable in caplog.text
    assert not any(secret in caplog.text for secret in SECRETS)


def test_a_missing_anthropic_key_exits_two_naming_it(env, monkeypatch, caplog, anthropic_config):
    result = run_main(config_path=anthropic_config, monkeypatch=monkeypatch)
    assert result.code == 2 and "conn" not in result.seen
    assert ANTHROPIC_KEY in caplog.text
    assert not any(secret in caplog.text for secret in SECRETS)


@pytest.mark.parametrize("value", ["http://qwen.example.test", "ftp://x.test", "qwen.example.test"])
def test_a_non_https_base_url_exits_two_without_echoing_it(env, monkeypatch, caplog, value):
    monkeypatch.setenv(QWEN_URL, value)
    with caplog.at_level(logging.INFO):
        result = run_main(monkeypatch=monkeypatch)
    assert result.code == 2 and "conn" not in result.seen
    assert value not in caplog.text
