"""trading_agent.llm.settings: the shared model section and provider variable names
(specs/008-portfolio-manager research P5; moved from research/config.py and __main__.py)."""

from __future__ import annotations

import pytest

from trading_agent.llm.settings import (
    ModelSettings,
    ModelSettingsError,
    parse_model_settings,
    provider_variables,
    require_https_base_url,
)

BOUNDS = (30, 360)


def section(**changes):
    base = {
        "provider": "qwen",
        "name": "qwen3.7-plus",
        "max_output_tokens": 8000,
        "timeout_seconds": 150,
        "anthropic_effort": "medium",
    }
    base.update(changes)
    return base


def parse(**changes):
    return parse_model_settings(section(**changes), timeout_bounds=BOUNDS)


def test_a_valid_section_parses_to_settings():
    assert parse() == ModelSettings("qwen", "qwen3.7-plus", 8000, 150, "medium")


def test_an_anthropic_section_parses():
    settings = parse(provider="anthropic", name="claude-sonnet-5-5", anthropic_effort="high")
    assert (settings.provider, settings.name, settings.anthropic_effort) == (
        "anthropic",
        "claude-sonnet-5-5",
        "high",
    )


def test_the_section_must_be_a_mapping():
    with pytest.raises(ModelSettingsError, match="model: must be a mapping"):
        parse_model_settings(["qwen"], timeout_bounds=BOUNDS)


def test_a_missing_key_is_named():
    data = section()
    del data["name"]
    with pytest.raises(ModelSettingsError, match=r"model\.name: required setting is missing"):
        parse_model_settings(data, timeout_bounds=BOUNDS)


def test_an_unknown_key_is_named():
    with pytest.raises(ModelSettingsError, match=r"model\.temperature: unknown setting"):
        parse(temperature=1)


@pytest.mark.parametrize(
    ("key", "low", "high"),
    [("max_output_tokens", 1000, 64_000), ("timeout_seconds", *BOUNDS)],
)
def test_integer_bounds_are_inclusive(key, low, high):
    assert getattr(parse(**{key: low}), key) == low
    assert getattr(parse(**{key: high}), key) == high
    for bad in (low - 1, high + 1):
        with pytest.raises(ModelSettingsError, match=rf"model\.{key}: {bad} is outside"):
            parse(**{key: bad})


@pytest.mark.parametrize("bad", ["150", 150.0, True, None])
def test_integers_must_be_integers(bad):
    with pytest.raises(ModelSettingsError, match=r"model\.timeout_seconds: must be an integer"):
        parse(timeout_seconds=bad)


def test_the_timeout_bounds_are_the_callers():
    narrow = parse_model_settings(section(timeout_seconds=60), timeout_bounds=(60, 90))
    assert narrow.timeout_seconds == 60
    with pytest.raises(ModelSettingsError, match="outside 60–90"):
        parse_model_settings(section(timeout_seconds=100), timeout_bounds=(60, 90))


def test_provider_name_and_effort_rules():
    with pytest.raises(ModelSettingsError, match=r"model\.provider: must be one of"):
        parse(provider="openai")
    for bad in ("", "  ", None, 5):
        with pytest.raises(ModelSettingsError, match=r"model\.name: must be a non-empty"):
            parse(name=bad)
    with pytest.raises(ModelSettingsError, match="claude-"):
        parse(provider="anthropic")  # the name is still qwen3.7-plus
    for bad in ("max", "", None):
        with pytest.raises(ModelSettingsError, match=r"model\.anthropic_effort: must be one of"):
            parse(anthropic_effort=bad)


def test_provider_variables_carry_the_agents_prefix():
    assert provider_variables("RESEARCH_", "qwen") == (
        "RESEARCH_DASHSCOPE_API_KEY",
        "RESEARCH_QWEN_BASE_URL",
    )
    assert provider_variables("PORTFOLIO_MANAGER_", "anthropic") == (
        "PORTFOLIO_MANAGER_ANTHROPIC_API_KEY",
    )


def test_provider_variables_refuse_an_unknown_provider():
    with pytest.raises(ModelSettingsError, match="model.provider"):
        provider_variables("RESEARCH_", "openai")


def test_an_https_base_url_passes_unchanged():
    url = "https://qwen.example.test/compatible-mode/v1"
    assert require_https_base_url(url, "X_QWEN_BASE_URL") == url


@pytest.mark.parametrize(
    "value",
    ["http://qwen.example.test", "https://", " https://x.example.test", "qwen.example.test", ""],
)
def test_other_base_urls_are_refused_by_variable_name_only(value):
    with pytest.raises(ModelSettingsError) as info:
        require_https_base_url(value, "X_QWEN_BASE_URL")
    assert str(info.value) == "X_QWEN_BASE_URL must be an https:// URL"
