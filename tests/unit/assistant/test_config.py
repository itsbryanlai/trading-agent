"""config/assistant.yaml loading (specs/013 tasks T002; contracts/assistant-interface.md)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from trading_agent.assistant.config import (
    DEFAULT_CONFIG_PATH,
    AssistantConfig,
    AssistantConfigError,
    load_config,
    provider_variables,
)

ROOT = Path(__file__).resolve().parents[3]


def _shipped() -> dict:
    return yaml.safe_load((ROOT / "config" / "assistant.yaml").read_text())


def _load(tmp_path, data) -> AssistantConfig:
    path = tmp_path / "assistant.yaml"
    path.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)
    return load_config(path)


def _with(path: str, value):
    data = deepcopy(_shipped())
    *parents, last = path.split(".")
    node = data
    for part in parents:
        node = node[part]
    node[last] = value
    return data


def _without(path: str):
    data = deepcopy(_shipped())
    *parents, last = path.split(".")
    node = data
    for part in parents:
        node = node[part]
    del node[last]
    return data


def test_the_default_path_is_the_shipped_file():
    assert DEFAULT_CONFIG_PATH == ROOT / "config" / "assistant.yaml"


def test_the_shipped_file_loads_with_the_contract_defaults():
    config = load_config()
    assert config.owner_time_zone == "Asia/Singapore"
    assert config.model.provider == "qwen"
    assert config.model.name == "qwen3.7-plus"
    assert config.model.max_output_tokens == 2000
    assert config.model.timeout_seconds == 45
    assert config.model.anthropic_effort == "low"
    assert config.action_words == (
        "pause", "resume", "unpause", "halt", "buy", "sell", "cancel", "trade", "liquidate",
    )
    assert (config.memory.exchanges, config.memory.idle_minutes) == (5, 30)
    assert config.limits.max_queries_per_question == 4
    assert config.limits.max_rows_per_query == 50
    assert config.limits.max_span_days == 92
    assert config.limits.max_input_chars == 60000
    assert config.limits.text_field_chars == 1500
    assert config.limits.max_reply_chars == 12000
    assert config.polling.long_poll_seconds == 50
    assert config.database.statement_timeout_seconds == 10
    assert config.database.max_consecutive_failures == 3


KEYS = [
    "owner_time_zone", "model", "action_words", "memory", "limits", "polling", "database",
    "model.provider", "model.name", "model.max_output_tokens", "model.timeout_seconds",
    "model.anthropic_effort", "memory.exchanges", "memory.idle_minutes",
    "limits.max_queries_per_question", "limits.max_rows_per_query", "limits.max_span_days",
    "limits.max_input_chars", "limits.text_field_chars", "limits.max_reply_chars",
    "polling.long_poll_seconds", "database.statement_timeout_seconds",
    "database.max_consecutive_failures",
]


@pytest.mark.parametrize("key", KEYS)
def test_a_missing_key_is_refused_by_name(tmp_path, key):
    with pytest.raises(AssistantConfigError, match=key.replace(".", r"\.")):
        _load(tmp_path, _without(key))


@pytest.mark.parametrize("section", ["", "model", "memory", "limits", "polling", "database"])
def test_an_unknown_key_is_refused_by_name(tmp_path, section):
    data = deepcopy(_shipped())
    (data[section] if section else data)["surprise"] = 1
    expected = f"{section}.surprise" if section else "surprise"
    with pytest.raises(AssistantConfigError, match=expected.replace(".", r"\.")):
        _load(tmp_path, data)


INT_BOUNDS = {
    "model.max_output_tokens": (1000, 64000),
    "model.timeout_seconds": (10, 120),
    "memory.exchanges": (0, 20),
    "memory.idle_minutes": (1, 240),
    "limits.max_queries_per_question": (1, 10),
    "limits.max_rows_per_query": (1, 500),
    "limits.max_span_days": (1, 366),
    "limits.max_input_chars": (5000, 400000),
    "limits.text_field_chars": (100, 10000),
    "limits.max_reply_chars": (500, 40000),
    "polling.long_poll_seconds": (10, 55),
    "database.statement_timeout_seconds": (1, 60),
    "database.max_consecutive_failures": (1, 20),
}


@pytest.mark.parametrize("key", INT_BOUNDS)
def test_integers_are_bounded_and_the_edges_load(tmp_path, key):
    low, high = INT_BOUNDS[key]
    pattern = key.replace(".", r"\.")
    for edge in (low, high):
        _load(tmp_path, _with(key, edge))
    for outside in (low - 1, high + 1):
        with pytest.raises(AssistantConfigError, match=pattern):
            _load(tmp_path, _with(key, outside))


@pytest.mark.parametrize("key", INT_BOUNDS)
@pytest.mark.parametrize("value", [True, "5", 5.0, None])
def test_integers_are_not_booleans_strings_or_floats(tmp_path, key, value):
    with pytest.raises(AssistantConfigError, match=key.replace(".", r"\.")):
        _load(tmp_path, _with(key, value))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("model.provider", "openai"),
        ("model.name", ""),
        ("model.anthropic_effort", "extreme"),
    ],
)
def test_a_bad_model_setting_is_refused_by_name(tmp_path, key, value):
    with pytest.raises(AssistantConfigError, match=key.replace(".", r"\.")):
        _load(tmp_path, _with(key, value))


def test_an_anthropic_model_name_must_start_with_claude(tmp_path):
    data = _with("model.provider", "anthropic")
    with pytest.raises(AssistantConfigError, match="model.name"):
        _load(tmp_path, data)
    data["model"]["name"] = "claude-sonnet-5-5"
    assert _load(tmp_path, data).model.provider == "anthropic"


@pytest.mark.parametrize("zone", ["Mars/Olympus", "", 5, None, "../etc/passwd"])
def test_an_unusable_time_zone_is_refused(tmp_path, zone):
    with pytest.raises(AssistantConfigError, match="owner_time_zone"):
        _load(tmp_path, _with("owner_time_zone", zone))


def test_another_real_zone_loads(tmp_path):
    assert _load(tmp_path, _with("owner_time_zone", "America/New_York")).owner_time_zone == (
        "America/New_York"
    )


@pytest.mark.parametrize(
    "words",
    [[], "pause", ["Pause"], ["pa use"], ["pause2"], ["pause", "pause"], [5], [""],
     [f"w{'a' * i}" for i in range(31)], ["a" * i for i in range(1, 32)]],
)
def test_bad_action_words_are_refused(tmp_path, words):
    with pytest.raises(AssistantConfigError, match="action_words"):
        _load(tmp_path, _with("action_words", words))


def test_one_and_thirty_action_words_load(tmp_path):
    thirty = ["a" * i for i in range(1, 31)]
    assert _load(tmp_path, _with("action_words", ["pause"])).action_words == ("pause",)
    assert len(_load(tmp_path, _with("action_words", thirty)).action_words) == 30


@pytest.mark.parametrize("text", ["", "- a\n- b\n", "key: [unclosed"])
def test_a_file_that_is_not_a_mapping_is_refused(tmp_path, text):
    with pytest.raises(AssistantConfigError):
        _load(tmp_path, text)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(AssistantConfigError, match="cannot read"):
        load_config(tmp_path / "nope.yaml")


def test_the_config_is_frozen():
    with pytest.raises(AttributeError):
        load_config().memory.exchanges = 9  # type: ignore[misc]


def test_provider_variables_use_the_assistant_prefix():
    assert provider_variables("qwen") == ("ASSISTANT_DASHSCOPE_API_KEY", "ASSISTANT_QWEN_BASE_URL")
    assert provider_variables("anthropic") == ("ASSISTANT_ANTHROPIC_API_KEY",)
    with pytest.raises(AssistantConfigError):
        provider_variables("openai")
