"""config/schedule.yaml is strict, bounded by ADR 0011, and applies the prefix rule
(research O4, O13; FR-008, FR-015)."""

from __future__ import annotations

from datetime import time, timedelta

import pytest
import yaml

from trading_agent.orchestrator.config import (
    DEFAULT_CONFIG_PATH,
    ScheduleConfigError,
    load_config,
)


def _base() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())


def _load(tmp_path, data) -> object:
    path = tmp_path / "schedule.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_config(path)


def _with(tmp_path, agent, key, value):
    data = _base()
    if value is _DELETE:
        del data[agent][key]
    else:
        data[agent][key] = value
    return _load(tmp_path, data)


_DELETE = object()


def test_shipped_file_loads_with_every_agent_disabled():
    config = load_config(DEFAULT_CONFIG_PATH)
    assert all(not agent.enabled for agent in config.agents())
    assert config.research.daily_at == time(8, 30) and config.research.interval is None
    assert config.identifier.interval == timedelta(minutes=60)
    assert config.identifier.window_start == time(10, 0)
    assert config.identifier.window_end == time(15, 0)
    pm = config.portfolio_manager
    assert pm.morning_session == time(10, 0) and pm.last_start == time(15, 30)
    assert pm.min_spacing == timedelta(minutes=30) and pm.report_wait == timedelta(minutes=5)
    assert pm.before_close == timedelta(minutes=30)
    assert config.research.timeout == timedelta(minutes=15)


def test_listed_variables_with_the_agents_prefix_are_accepted(tmp_path):
    config = _with(tmp_path, "research", "env", ["RESEARCH_ANTHROPIC_API_KEY", "RESEARCH_DB"])
    assert config.research.env == ("RESEARCH_ANTHROPIC_API_KEY", "RESEARCH_DB")


@pytest.mark.parametrize("agent", ["research", "opportunistic_identifier", "portfolio_manager"])
@pytest.mark.parametrize(
    "name",
    [
        "ALPACA_API_KEY_ID",
        "EXECUTION_DATABASE_URL",
        "ADMIN_DATABASE_URL",
        "RISK_GATE_DATABASE_URL",
        "REFERENCE_DATA_FINNHUB_API_KEY",
        "ORCHESTRATOR_DATABASE_URL",
        "PATH",
        "research_lowercase",
    ],
)
def test_the_prefix_rule_rejects_other_components_variables(tmp_path, agent, name):
    with pytest.raises(ScheduleConfigError):
        _with(tmp_path, agent, "env", [name])


def test_another_agents_prefix_is_rejected(tmp_path):
    with pytest.raises(ScheduleConfigError):
        _with(tmp_path, "research", "env", ["PORTFOLIO_MANAGER_KEY"])
    with pytest.raises(ScheduleConfigError):
        _with(tmp_path, "portfolio_manager", "env", ["RESEARCH_KEY"])


@pytest.mark.parametrize(
    ("agent", "key", "value"),
    [
        ("research", "enabled", "yes"),
        ("research", "module", "os.system"),
        ("research", "module", "trading_agent.Research"),
        ("research", "env", "RESEARCH_KEY"),
        ("research", "env", ["RESEARCH_KEY", "RESEARCH_KEY"]),
        ("research", "env", [1]),
        ("research", "timeout_minutes", 0),
        ("research", "timeout_minutes", 121),
        ("research", "timeout_minutes", True),
        ("research", "timeout_minutes", 5.5),
        ("research", "daily_at", "8:30"),
        ("research", "daily_at", "25:00"),
        ("research", "daily_at", "09:30"),  # not before the open
        ("research", "interval_minutes", 0),
        ("opportunistic_identifier", "interval_minutes", 0),
        ("opportunistic_identifier", "interval_minutes", None),
        ("opportunistic_identifier", "window_end", "10:00"),  # not after window_start
        ("portfolio_manager", "min_spacing_minutes", 29),
        ("portfolio_manager", "last_start", "15:31"),
        ("portfolio_manager", "before_close_minutes", 29),
        ("portfolio_manager", "report_wait_minutes", 4),
        ("portfolio_manager", "report_wait_minutes", 61),
        ("portfolio_manager", "morning_session", "09:29"),
        ("portfolio_manager", "morning_session", "15:30"),  # not before last_start
        ("portfolio_manager", "timeout_minutes", 31),  # longer than before_close (M2)
        ("portfolio_manager", "morning_session", "12:30"),  # at the early-close cutoff (L4)
        ("opportunistic_identifier", "interval_minutes", 10),  # not longer than its timeout
        ("research", "interval_minutes", 15),  # not longer than its timeout
        ("portfolio_manager", "enabled", _DELETE),
        ("portfolio_manager", "surprise", 1),
    ],
)
def test_bad_values_are_rejected(tmp_path, agent, key, value):
    with pytest.raises(ScheduleConfigError):
        _with(tmp_path, agent, key, value)


@pytest.mark.parametrize(
    ("agent", "key", "value"),
    [
        ("portfolio_manager", "min_spacing_minutes", 30),
        ("portfolio_manager", "last_start", "15:30"),
        ("portfolio_manager", "before_close_minutes", 30),
        ("portfolio_manager", "report_wait_minutes", 5),
        ("portfolio_manager", "report_wait_minutes", 60),
        ("portfolio_manager", "morning_session", "09:30"),
        ("portfolio_manager", "timeout_minutes", 30),
        ("portfolio_manager", "morning_session", "12:29"),
        ("opportunistic_identifier", "interval_minutes", 11),
        ("research", "interval_minutes", 16),
        ("research", "daily_at", "09:29"),
        ("research", "interval_minutes", 120),
        ("research", "timeout_minutes", 1),
        ("research", "timeout_minutes", 120),
    ],
)
def test_boundaries_are_allowed(tmp_path, agent, key, value):
    _with(tmp_path, agent, key, value)


def test_missing_agent_and_unknown_agent_are_rejected(tmp_path):
    data = _base()
    del data["research"]
    with pytest.raises(ScheduleConfigError):
        _load(tmp_path, data)
    data = _base()
    data["execution"] = {}
    with pytest.raises(ScheduleConfigError):
        _load(tmp_path, data)


def test_unreadable_files_are_rejected(tmp_path):
    with pytest.raises(ScheduleConfigError):
        load_config(tmp_path / "absent.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("research: [\n")
    with pytest.raises(ScheduleConfigError):
        load_config(bad)
    bad.write_text("- a list\n")
    with pytest.raises(ScheduleConfigError):
        load_config(bad)
