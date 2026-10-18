"""The deployed run_while_paused setting (specs/014 tasks T011; research D14)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.dashboard import deployed

ROOT = Path(__file__).resolve().parents[3]


def _write(tmp_path, text: str) -> Path:
    (tmp_path / "schedule.yaml").write_text(text)
    return tmp_path


def test_the_default_directory_is_the_repositorys_config():
    assert deployed.DEFAULT_CONFIG_DIR == ROOT / "config"


def test_the_real_schedule_says_true_while_observing():
    assert deployed.run_while_paused(ROOT / "config") is True


def test_the_default_directory_reads_the_real_schedule():
    assert deployed.run_while_paused() is True


@pytest.mark.parametrize("value", [True, False])
def test_a_real_boolean_is_returned(tmp_path, value):
    text = f"portfolio_manager:\n  run_while_paused: {str(value).lower()}\n"
    assert deployed.run_while_paused(_write(tmp_path, text)) is value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "- a\n- b\n",
        "just text",
        "portfolio_manager: [unclosed",
        "research:\n  enabled: true\n",  # no portfolio_manager section
        "portfolio_manager: null\n",
        "portfolio_manager: true\n",  # the section is not a mapping
        "portfolio_manager:\n  enabled: true\n",  # no key
        'portfolio_manager:\n  run_while_paused: "true"\n',
        'portfolio_manager:\n  run_while_paused: "false"\n',
        "portfolio_manager:\n  run_while_paused: 1\n",
        "portfolio_manager:\n  run_while_paused: 0\n",
        "portfolio_manager:\n  run_while_paused: null\n",
        "portfolio_manager:\n  run_while_paused: yes please\n",
        "portfolio_manager:\n  run_while_paused: [true]\n",
        "portfolio_manager:\n  run_while_paused: !!python/object/apply:os.getcwd []\n",
    ],
)
def test_anything_but_a_real_boolean_is_unreadable(tmp_path, text):
    assert deployed.run_while_paused(_write(tmp_path, text)) is None


def test_a_missing_file_or_directory_is_unreadable(tmp_path):
    assert deployed.run_while_paused(tmp_path) is None
    assert deployed.run_while_paused(tmp_path / "nowhere") is None


def test_a_file_that_is_not_text_is_unreadable(tmp_path):
    (tmp_path / "schedule.yaml").write_bytes(b"\xff\xfe\x00 not utf-8 \x80")
    assert deployed.run_while_paused(tmp_path) is None
