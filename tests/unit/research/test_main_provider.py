"""Only the configured provider's key is needed, and only the prefixed one is ever
read (specs/007-research-agent FR-018; ADR 0015, 0018)."""

from __future__ import annotations

import yaml

from tests.unit.research.conftest import FAKE_ANTHROPIC, FAKE_DASHSCOPE
from tests.unit.research.test_main import run
from trading_agent.research import __main__ as runner
from trading_agent.research.anthropic_client import AnthropicClient
from trading_agent.research.config import DEFAULT_CONFIG_PATH
from trading_agent.research.qwen import QwenClient


def anthropic_config(tmp_path):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    data["model"]["provider"] = "anthropic"
    data["model"]["name"] = "claude-sonnet-5-5"
    path = tmp_path / "research.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_qwen_needs_only_the_dashscope_key(env):
    code, _, _, seen = run()
    assert code == runner.EXIT_OK
    assert seen["model"] == ("qwen", FAKE_DASHSCOPE, "qwen3.7-plus")


def test_anthropic_needs_only_its_own_key(env, monkeypatch, tmp_path):
    monkeypatch.delenv("RESEARCH_DASHSCOPE_API_KEY")
    monkeypatch.setenv("RESEARCH_ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    code, _, _, seen = run(config_path=anthropic_config(tmp_path))
    assert code == runner.EXIT_OK
    assert seen["model"] == ("anthropic", FAKE_ANTHROPIC, "claude-sonnet-5-5")


def test_a_missing_anthropic_key_refuses_to_start_naming_it(env, tmp_path, caplog):
    code, _, _, seen = run(config_path=anthropic_config(tmp_path))
    assert code == runner.EXIT_REFUSED
    assert "RESEARCH_ANTHROPIC_API_KEY" in caplog.text and "model" not in seen


def test_the_unprefixed_anthropic_key_is_never_used(env, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    code, _, printed, seen = run(config_path=anthropic_config(tmp_path))
    assert code == runner.EXIT_REFUSED
    assert "model" not in seen
    assert FAKE_ANTHROPIC not in caplog.text + "\n".join(printed)


def test_build_model_picks_the_adapter(monkeypatch):
    from tests.unit.research.support import config

    qwen = runner.build_model("qwen", "k", config().model)
    assert isinstance(qwen, QwenClient)

    class FakeSDK:
        def __init__(self, **kwargs):
            self.messages = None

    monkeypatch.setattr("trading_agent.research.anthropic_client.anthropic.Anthropic", FakeSDK)
    model = config(model_provider="anthropic", model_name="claude-sonnet-5-5").model
    assert isinstance(runner.build_model("anthropic", "k", model), AnthropicClient)
