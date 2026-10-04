"""config/portfolio_manager.yaml loads strictly (specs/008-portfolio-manager research P11).

The bounds table and the cross-checks are User Story 5's; this pins the loader's shape."""

from __future__ import annotations

import pytest
import yaml

from trading_agent.portfolio_manager.config import (
    DEFAULT_CONFIG_PATH,
    PortfolioManagerConfigError,
    load_config,
)


def shipped() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())


def load(tmp_path, data):
    path = tmp_path / "pm.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_config(path)


def test_the_shipped_file_loads_with_the_documented_defaults():
    cfg = load_config()
    assert (cfg.quote_max_age_minutes, cfg.finnhub_calls_per_minute) == (5, 30)
    assert (cfg.quote_phase_seconds, cfg.journal_entries) == (90, 5)
    assert (cfg.journal_summary_max_chars, cfg.rationale_max_chars) == (2000, 2000)
    assert (cfg.reasoning_max_chars, cfg.max_input_chars) == (2000, 300000)
    assert (cfg.model.provider, cfg.model.name) == ("qwen", "qwen3.7-plus")
    assert (cfg.model.max_output_tokens, cfg.model.timeout_seconds) == (8000, 150)
    assert cfg.provider_variables == (
        "PORTFOLIO_MANAGER_DASHSCOPE_API_KEY",
        "PORTFOLIO_MANAGER_QWEN_BASE_URL",
    )


def test_the_anthropic_provider_needs_only_its_own_key(tmp_path):
    data = shipped()
    data["model"].update(provider="anthropic", name="claude-sonnet-5-5")
    assert load(tmp_path, data).provider_variables == ("PORTFOLIO_MANAGER_ANTHROPIC_API_KEY",)


def test_a_missing_or_unknown_key_is_named(tmp_path):
    data = shipped()
    del data["journal_entries"]
    with pytest.raises(PortfolioManagerConfigError, match="journal_entries: required"):
        load(tmp_path, data)
    data = shipped()
    data["surprise"] = 1
    with pytest.raises(PortfolioManagerConfigError, match="surprise: unknown setting"):
        load(tmp_path, data)


def test_ints_are_checked_for_type_and_range(tmp_path):
    for key, bad, match in (
        ("quote_max_age_minutes", 11, "outside 1–10"),
        ("quote_max_age_minutes", 0, "outside 1–10"),
        ("journal_entries", -1, "outside 0–20"),
        ("max_input_chars", 9999, "outside"),
        ("quote_phase_seconds", "90", "must be an integer"),
        ("finnhub_calls_per_minute", True, "must be an integer"),
    ):
        data = shipped()
        data[key] = bad
        with pytest.raises(PortfolioManagerConfigError, match=match):
            load(tmp_path, data)


def test_the_model_section_uses_the_pms_own_timeout_bounds(tmp_path):
    data = shipped()
    data["model"]["timeout_seconds"] = 240
    assert load(tmp_path, data).model.timeout_seconds == 240
    data["model"]["timeout_seconds"] = 241
    with pytest.raises(PortfolioManagerConfigError, match=r"model\.timeout_seconds: 241"):
        load(tmp_path, data)


def test_a_model_error_names_its_key(tmp_path):
    data = shipped()
    data["model"]["provider"] = "openai"
    with pytest.raises(PortfolioManagerConfigError, match=r"model\.provider"):
        load(tmp_path, data)


def test_a_file_that_cannot_be_read_is_refused(tmp_path):
    with pytest.raises(PortfolioManagerConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")
    (tmp_path / "list.yaml").write_text("- 1\n")
    with pytest.raises(PortfolioManagerConfigError, match="must be a mapping"):
        load_config(tmp_path / "list.yaml")
