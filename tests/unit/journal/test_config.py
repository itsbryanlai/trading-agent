"""config/journal.yaml loader (contracts/journal-interface.md "Configuration")."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trading_agent.journal.config import (
    DEFAULT_CONFIG_PATH,
    JournalConfigError,
    load_config,
)

GOOD = {
    "holding_sessions": 5,
    "finnhub_calls_per_minute": 20,
    "fetch_deadline_seconds": 480,
    "close_grace_minutes": 5,
}


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "journal.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_the_shipped_file_loads_with_the_documented_defaults():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert (
        cfg.holding_sessions,
        cfg.finnhub_calls_per_minute,
        cfg.fetch_deadline_seconds,
        cfg.close_grace_minutes,
    ) == (5, 20, 480, 5)


def test_the_config_is_frozen():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    with pytest.raises(AttributeError):
        cfg.holding_sessions = 6  # type: ignore[misc]


@pytest.mark.parametrize("key", sorted(GOOD))
def test_a_missing_key_is_refused_by_name(tmp_path, key):
    data = {k: v for k, v in GOOD.items() if k != key}
    with pytest.raises(JournalConfigError, match=key):
        load_config(_write(tmp_path, data))


def test_an_unknown_key_is_refused_by_name(tmp_path):
    with pytest.raises(JournalConfigError, match="surprise"):
        load_config(_write(tmp_path, {**GOOD, "surprise": 1}))


@pytest.mark.parametrize("key", sorted(GOOD))
@pytest.mark.parametrize("bad", [True, "5", 5.0, None])
def test_a_wrong_typed_value_is_refused_by_name(tmp_path, key, bad):
    with pytest.raises(JournalConfigError, match=key):
        load_config(_write(tmp_path, {**GOOD, key: bad}))


BOUNDS = {
    "holding_sessions": (1, 60),
    "finnhub_calls_per_minute": (1, 300),
    "fetch_deadline_seconds": (30, 540),
    "close_grace_minutes": (0, 30),
}


@pytest.mark.parametrize("key", sorted(BOUNDS))
def test_values_outside_the_bounds_are_refused_and_the_edges_accepted(tmp_path, key):
    low, high = BOUNDS[key]
    for edge in (low, high):
        assert load_config(_write(tmp_path, {**GOOD, key: edge}))
    for outside in (low - 1, high + 1):
        with pytest.raises(JournalConfigError, match=key):
            load_config(_write(tmp_path, {**GOOD, key: outside}))


def test_a_file_that_is_not_a_mapping_is_refused(tmp_path):
    with pytest.raises(JournalConfigError):
        load_config(_write(tmp_path, [1, 2]))


def test_an_unreadable_file_is_refused(tmp_path):
    with pytest.raises(JournalConfigError):
        load_config(tmp_path / "missing.yaml")
