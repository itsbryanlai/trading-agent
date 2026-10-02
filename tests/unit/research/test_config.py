"""config/research.yaml is strict and bounded (specs/007-research-agent research R11)."""

from __future__ import annotations

import copy

import pytest
import yaml

from trading_agent.research.config import (
    DEFAULT_CONFIG_PATH,
    ResearchConfigError,
    load_config,
)


def shipped() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())


def write(tmp_path, data) -> object:
    path = tmp_path / "research.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def load(tmp_path, data):
    return load_config(write(tmp_path, data))


def changed(path: tuple, value) -> dict:
    data = copy.deepcopy(shipped())
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


def test_shipped_file_loads():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.watchlist == ()
    assert cfg.general_news_max_articles == 20
    assert cfg.articles_per_symbol == 5
    assert cfg.model.provider == "qwen"
    assert cfg.model.name == "qwen3.7-plus"
    assert cfg.model.anthropic_effort == "medium"
    assert cfg.provider_key_variable == "RESEARCH_DASHSCOPE_API_KEY"


def test_anthropic_provider_names_its_own_key(tmp_path):
    data = changed(("model", "provider"), "anthropic")
    data["model"]["name"] = "claude-sonnet-5-5"
    assert load(tmp_path, data).provider_key_variable == "RESEARCH_ANTHROPIC_API_KEY"


def test_unreadable_or_not_a_mapping(tmp_path):
    with pytest.raises(ResearchConfigError):
        load_config(tmp_path / "missing.yaml")
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n")
    with pytest.raises(ResearchConfigError, match="mapping"):
        load_config(path)


@pytest.mark.parametrize("key", ["watchlist", "max_input_chars", "model"])
def test_missing_top_level_key(tmp_path, key):
    data = shipped()
    del data[key]
    with pytest.raises(ResearchConfigError, match=key):
        load(tmp_path, data)


@pytest.mark.parametrize("key", ["provider", "name", "timeout_seconds", "anthropic_effort"])
def test_missing_model_key(tmp_path, key):
    data = shipped()
    del data["model"][key]
    with pytest.raises(ResearchConfigError, match=key):
        load(tmp_path, data)


def test_unknown_keys_at_both_levels(tmp_path):
    with pytest.raises(ResearchConfigError, match="surprise"):
        load(tmp_path, {**shipped(), "surprise": 1})
    data = shipped()
    data["model"]["qwen_enable_thinking"] = True
    with pytest.raises(ResearchConfigError, match="qwen_enable_thinking"):
        load(tmp_path, data)


BOUNDS = [
    (("general_news_max_articles",), 0, 100),
    (("articles_per_symbol",), 1, 20),
    (("article_summary_max_chars",), 100, 5000),
    (("max_input_chars",), 10000, 2000000),
    (("rationale_max_chars",), 200, 10000),
    (("finnhub_calls_per_minute",), 1, 300),
    (("model", "max_output_tokens"), 1000, 64000),
    (("model", "timeout_seconds"), 30, 360),
]


@pytest.mark.parametrize(("path", "low", "high"), BOUNDS)
def test_integer_bounds(tmp_path, path, low, high):
    load(tmp_path, changed(path, low))
    load(tmp_path, changed(path, high))
    for bad in (low - 1, high + 1):
        with pytest.raises(ResearchConfigError, match=path[-1]):
            load(tmp_path, changed(path, bad))


@pytest.mark.parametrize(("path", "low", "high"), BOUNDS)
def test_booleans_and_strings_are_not_integers(tmp_path, path, low, high):
    for bad in (True, str(low), float(low) + 0.5):
        with pytest.raises(ResearchConfigError, match=path[-1]):
            load(tmp_path, changed(path, bad))


@pytest.mark.parametrize("bad", ["aapl", "TOOLONG", "BRK/B", "", 5])
def test_watchlist_entries_must_be_tickers(tmp_path, bad):
    with pytest.raises(ResearchConfigError, match="watchlist"):
        load(tmp_path, changed(("watchlist",), ["AAPL", bad]))


def test_watchlist_rejects_duplicates_and_non_lists(tmp_path):
    with pytest.raises(ResearchConfigError, match="duplicate"):
        load(tmp_path, changed(("watchlist",), ["AAPL", "AAPL"]))
    with pytest.raises(ResearchConfigError, match="watchlist"):
        load(tmp_path, changed(("watchlist",), "AAPL"))


def test_watchlist_accepts_share_classes_and_at_most_50(tmp_path):
    cfg = load(tmp_path, changed(("watchlist",), ["AAPL", "BRK.B", "BF-B"]))
    assert cfg.watchlist == ("AAPL", "BRK.B", "BF-B")
    many = [f"{a}{b}" for a in "ABCDEFGHIJ" for b in "KLMNOP"]  # 60 two-letter tickers
    with pytest.raises(ResearchConfigError, match="50"):
        load(tmp_path, changed(("watchlist",), many[:51]))


def test_provider_and_name_rules(tmp_path):
    with pytest.raises(ResearchConfigError, match="provider"):
        load(tmp_path, changed(("model", "provider"), "openai"))
    with pytest.raises(ResearchConfigError, match="name"):
        load(tmp_path, changed(("model", "name"), ""))
    data = changed(("model", "provider"), "anthropic")  # name still qwen3.7-plus
    with pytest.raises(ResearchConfigError, match="claude-"):
        load(tmp_path, data)


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_anthropic_effort_values(tmp_path, effort):
    cfg = load(tmp_path, changed(("model", "anthropic_effort"), effort))
    assert cfg.model.anthropic_effort == effort


@pytest.mark.parametrize("effort", ["max", "xhigh", "", None])
def test_anthropic_effort_rejects_others(tmp_path, effort):
    with pytest.raises(ResearchConfigError, match="anthropic_effort"):
        load(tmp_path, changed(("model", "anthropic_effort"), effort))
