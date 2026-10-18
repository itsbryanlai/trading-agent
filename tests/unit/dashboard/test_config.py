"""config/dashboard.yaml loading (specs/014 tasks T002; contracts/dashboard-interface.md)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from trading_agent.dashboard.config import (
    DEFAULT_CONFIG_PATH,
    DashboardConfig,
    DashboardConfigError,
    load_config,
)

ROOT = Path(__file__).resolve().parents[3]


def _shipped() -> dict:
    return yaml.safe_load((ROOT / "config" / "dashboard.yaml").read_text())


def _load(tmp_path, data) -> DashboardConfig:
    path = tmp_path / "dashboard.yaml"
    path.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)
    return load_config(path)


def _edit(path: str, value=..., *, delete=False):
    data = deepcopy(_shipped())
    *parents, last = path.split(".")
    node = data
    for part in parents:
        node = node[part]
    if delete:
        del node[last]
    else:
        node[last] = value
    return data


def test_the_default_path_is_the_shipped_file():
    assert DEFAULT_CONFIG_PATH == ROOT / "config" / "dashboard.yaml"


def test_the_shipped_file_loads_with_the_contract_defaults():
    config = load_config()
    assert config.owner_time_zone == "Asia/Singapore"
    assert config.session_hours == 12
    assert config.page_size == 50
    assert config.max_sessions == 20
    assert config.sign_in_delay.first_seconds == 1
    assert config.sign_in_delay.cap_seconds == 30
    assert config.sign_in_delay.tracked_addresses == 1000
    assert config.database.statement_timeout_seconds == 10


@pytest.mark.parametrize(
    "key",
    [
        "owner_time_zone",
        "session_hours",
        "page_size",
        "max_sessions",
        "sign_in_delay",
        "database",
        "sign_in_delay.first_seconds",
        "sign_in_delay.cap_seconds",
        "sign_in_delay.tracked_addresses",
        "database.statement_timeout_seconds",
    ],
)
def test_a_missing_key_is_refused_naming_the_key(tmp_path, key):
    with pytest.raises(DashboardConfigError, match=key.replace(".", r"\.")):
        _load(tmp_path, _edit(key, delete=True))


@pytest.mark.parametrize("path", ["", "sign_in_delay.", "database."])
def test_an_unknown_key_is_refused_naming_the_key(tmp_path, path):
    data = _shipped()
    node = data
    for part in [p for p in path.split(".") if p]:
        node = node[part]
    node["surprise"] = 1
    with pytest.raises(DashboardConfigError, match=f"{path}surprise".replace(".", r"\.")):
        _load(tmp_path, data)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("session_hours", 0),
        ("session_hours", 25),
        ("page_size", 9),
        ("page_size", 201),
        ("max_sessions", 0),
        ("max_sessions", 101),
        ("sign_in_delay.first_seconds", 0),
        ("sign_in_delay.first_seconds", 11),
        ("sign_in_delay.cap_seconds", 301),
        ("sign_in_delay.tracked_addresses", 9),
        ("sign_in_delay.tracked_addresses", 100001),
        ("database.statement_timeout_seconds", 0),
        ("database.statement_timeout_seconds", 61),
    ],
)
def test_an_out_of_bounds_value_is_refused_naming_the_key(tmp_path, key, value):
    with pytest.raises(DashboardConfigError, match=key.replace(".", r"\.")):
        _load(tmp_path, _edit(key, value))


@pytest.mark.parametrize(
    "key",
    [
        "session_hours",
        "page_size",
        "max_sessions",
        "sign_in_delay.first_seconds",
        "sign_in_delay.cap_seconds",
        "sign_in_delay.tracked_addresses",
        "database.statement_timeout_seconds",
    ],
)
@pytest.mark.parametrize("value", [True, False, "12", 1.5])
def test_a_boolean_string_or_float_for_an_integer_is_refused(tmp_path, key, value):
    with pytest.raises(DashboardConfigError, match=key.replace(".", r"\.")):
        _load(tmp_path, _edit(key, value))


@pytest.mark.parametrize("zone", ["Mars/Base", "", "   ", 5, None, "../etc/passwd"])
def test_a_bad_zone_is_refused(tmp_path, zone):
    with pytest.raises(DashboardConfigError, match="owner_time_zone"):
        _load(tmp_path, _edit("owner_time_zone", zone))


def test_the_cap_must_be_at_least_the_first_delay(tmp_path):
    data = _edit("sign_in_delay.first_seconds", 5)
    data["sign_in_delay"]["cap_seconds"] = 4
    with pytest.raises(DashboardConfigError, match=r"sign_in_delay\.cap_seconds"):
        _load(tmp_path, data)
    data["sign_in_delay"]["cap_seconds"] = 5
    assert _load(tmp_path, data).sign_in_delay.cap_seconds == 5


def test_a_section_that_is_not_a_mapping_is_refused(tmp_path):
    with pytest.raises(DashboardConfigError, match="sign_in_delay"):
        _load(tmp_path, _edit("sign_in_delay", 3))


def test_a_file_that_is_not_a_mapping_or_not_yaml_or_missing_is_refused(tmp_path):
    with pytest.raises(DashboardConfigError):
        _load(tmp_path, "- a\n- b\n")
    with pytest.raises(DashboardConfigError):
        _load(tmp_path, "a: [unclosed")
    with pytest.raises(DashboardConfigError):
        load_config(tmp_path / "missing.yaml")
