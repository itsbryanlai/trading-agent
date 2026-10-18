"""The settings entry reads allowlisted keys from the deployed files (specs/013 T015; A16)."""

from __future__ import annotations

import json

import pytest

from trading_agent.assistant.config import DEFAULT_CONFIG_PATH
from trading_agent.assistant.settings_view import SECTIONS, UNAVAILABLE, read

REAL_DIR = DEFAULT_CONFIG_PATH.parent


def test_the_real_config_files_load_every_section():
    result = read(REAL_DIR, None)
    assert set(result) == set(SECTIONS)
    assert result["risk"]["max_position_pct"] != UNAVAILABLE
    assert result["risk"]["universe"] != UNAVAILABLE
    assert result["schedule"]["research"]["enabled"] != UNAVAILABLE
    assert set(result["models"]) == {
        "research", "opportunistic_identifier", "portfolio_manager", "assistant"
    }
    assert result["models"]["assistant"]["provider"] != UNAVAILABLE


def test_one_section_returns_only_that_section():
    assert set(read(REAL_DIR, "risk")) == {"risk"}


def test_a_temporary_risk_file_shows_its_value(tmp_path):
    (tmp_path / "risk.yaml").write_text("max_position_pct: 8\nuniverse:\n  listing: x\n")
    risk = read(tmp_path, "risk")["risk"]
    assert risk["max_position_pct"] == 8
    assert risk["universe"] == {"listing": "x"}
    assert risk["stop_loss_pct"] == UNAVAILABLE


def test_unlisted_keys_and_env_never_appear(tmp_path):
    (tmp_path / "schedule.yaml").write_text(
        "research:\n  enabled: true\n  daily_at: '08:30'\n  secret_like_key: PLANTED-X\n"
        "  env: [RESEARCH_DATABASE_URL]\n  module: m\nsecret_like_key: PLANTED-Y\n"
    )
    (tmp_path / "risk.yaml").write_text("max_position_pct: 8\nsecret_like_key: PLANTED-Z\n")
    text = json.dumps(read(tmp_path, None))
    for banned in ("PLANTED", "RESEARCH_DATABASE_URL", "secret_like_key", "module"):
        assert banned not in text
    assert read(tmp_path, "schedule")["schedule"]["research"]["daily_at"] == "08:30"
    assert read(tmp_path, "schedule")["schedule"]["portfolio_manager"] == UNAVAILABLE


@pytest.mark.parametrize("content", ["a: [unclosed", "- just\n- a list\n", ""])
def test_a_malformed_file_is_unavailable_for_its_section(tmp_path, content):
    (tmp_path / "risk.yaml").write_text(content)
    assert read(tmp_path, "risk")["risk"] == UNAVAILABLE


def test_a_missing_file_is_unavailable(tmp_path):
    result = read(tmp_path, None)
    assert result["risk"] == UNAVAILABLE and result["schedule"] == UNAVAILABLE
    assert all(v == UNAVAILABLE for v in result["models"].values())


def test_a_non_scalar_value_under_a_scalar_key_is_not_copied(tmp_path):
    (tmp_path / "risk.yaml").write_text("max_position_pct: {a: PLANTED}\n")
    assert read(tmp_path, "risk")["risk"]["max_position_pct"] == UNAVAILABLE
