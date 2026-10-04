"""config/portfolio_manager.yaml loads strictly (specs/008-portfolio-manager research P11).

Bounds, shape and the two run-time cross-checks (research P11)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trading_agent.portfolio_manager import config as pm_config
from trading_agent.portfolio_manager.config import (
    DEFAULT_CONFIG_PATH,
    PortfolioManagerConfigError,
    load_config,
    worst_case_seconds,
)
from trading_agent.risk import __main__ as gate_loop
from trading_agent.risk.gate import MAX_DECISION_QUOTE_AGE

SCHEDULE = Path(__file__).resolve().parents[3] / "config" / "schedule.yaml"


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
    assert (cfg.source_title_max_chars, cfg.sources_per_report) == (300, 10)
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
    data["quote_phase_seconds"], data["quote_max_age_minutes"] = 10, 1
    data["model"]["timeout_seconds"] = 225  # 10 + 450 + 60 = 520: the longest that fits
    assert load(tmp_path, data).model.timeout_seconds == 225
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


BOUNDS = {
    "quote_max_age_minutes": (1, 10),
    "finnhub_calls_per_minute": (1, 300),
    "quote_phase_seconds": (10, 300),
    "journal_entries": (0, 20),
    "journal_summary_max_chars": (200, 10_000),
    "rationale_max_chars": (200, 10_000),
    "reasoning_max_chars": (200, 10_000),
    "source_title_max_chars": (50, 2_000),
    "sources_per_report": (1, 50),
    "max_input_chars": (10_000, 2_000_000),
}


@pytest.mark.parametrize("key", sorted(BOUNDS))
def test_every_bound_is_exact(tmp_path, key):
    low, high = BOUNDS[key]
    for value in (low, high):
        data = shipped()
        data[key] = value
        # Pull the run-time inputs down so only the bound under test can refuse the file.
        data["quote_phase_seconds"] = value if key == "quote_phase_seconds" else 10
        data["model"]["timeout_seconds"] = 30
        data["quote_max_age_minutes"] = value if key == "quote_max_age_minutes" else 1
        assert getattr(load(tmp_path, data), key) == value
    for value in (low - 1, high + 1):
        data = shipped()
        data[key] = value
        with pytest.raises(PortfolioManagerConfigError, match=f"{key}: {value} is outside"):
            load(tmp_path, data)


def test_the_model_bounds(tmp_path):
    for key, value in (
        ("max_output_tokens", 999),
        ("max_output_tokens", 64001),
        ("timeout_seconds", 29),
        ("anthropic_effort", "max"),
        ("name", ""),
    ):
        data = shipped()
        data["model"][key] = value
        with pytest.raises(PortfolioManagerConfigError, match=f"model.{key}"):
            load(tmp_path, data)
    data = shipped()
    data["model"].update(provider="anthropic", name="gpt-5")
    with pytest.raises(PortfolioManagerConfigError, match="model.name"):
        load(tmp_path, data)


def test_the_shipped_defaults_fit_both_cross_checks():
    cfg = load_config()
    worst = worst_case_seconds(cfg.quote_phase_seconds, cfg.model.timeout_seconds)
    assert worst == 450
    assert cfg.quote_max_age_minutes * 60 + worst + 120 == 870


def test_a_run_that_could_outlast_the_orchestrators_budget_is_refused(tmp_path):
    data = shipped()
    data["quote_phase_seconds"] = 300  # 300 + 300 + 60 = 660 > 540
    with pytest.raises(PortfolioManagerConfigError, match="over the orchestrator's 540 s"):
        load(tmp_path, data)


def test_the_orchestrator_boundary_is_540_seconds(tmp_path):
    data = shipped()
    data["quote_max_age_minutes"] = 1
    data["quote_phase_seconds"] = 180  # 180 + 300 + 60 = 540: allowed
    assert load(tmp_path, data).quote_phase_seconds == 180
    data["quote_phase_seconds"] = 181
    with pytest.raises(PortfolioManagerConfigError, match="orchestrator"):
        load(tmp_path, data)


def test_a_config_that_fits_the_orchestrator_but_not_the_gate_is_refused(tmp_path):
    data = shipped()
    data["quote_max_age_minutes"] = 7  # worst case 450 <= 540, but 420 + 450 + 120 = 990 > 900
    with pytest.raises(PortfolioManagerConfigError, match="990 s old at the gate"):
        load(tmp_path, data)


def test_the_gate_boundary_is_900_seconds(tmp_path):
    data = shipped()
    data["quote_max_age_minutes"] = 6  # 360 + 450 + 120 = 930: refused
    with pytest.raises(PortfolioManagerConfigError, match="930 s old at the gate"):
        load(tmp_path, data)
    data["quote_phase_seconds"] = 60  # 360 + (60 + 300 + 60) + 120 = 900: allowed
    assert load(tmp_path, data).quote_max_age_minutes == 6
    data["quote_phase_seconds"] = 61
    with pytest.raises(PortfolioManagerConfigError, match="901 s old at the gate"):
        load(tmp_path, data)


def test_the_copied_limits_match_their_sources():
    schedule = yaml.safe_load(SCHEDULE.read_text())
    assert pm_config.RUN_BUDGET_SECONDS == schedule["portfolio_manager"]["timeout_minutes"] * 60
    assert pm_config.GATE_STALENESS_SECONDS == MAX_DECISION_QUOTE_AGE.total_seconds()
    assert pm_config.GATE_WAIT_SECONDS == 2 * gate_loop.PASS_SECONDS
