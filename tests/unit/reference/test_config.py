"""config/reference_data.yaml is strict: a bad file refuses to start (research D11)."""

from __future__ import annotations

import pytest

from trading_agent.reference.config import (
    DEFAULT_CONFIG_PATH,
    ReferenceConfig,
    ReferenceConfigError,
    load_config,
)


def _write(tmp_path, text):
    path = tmp_path / "reference_data.yaml"
    path.write_text(text)
    return path


def test_shipped_file_loads_with_an_empty_seed_list():
    config = load_config(DEFAULT_CONFIG_PATH)
    assert config == ReferenceConfig(seed_symbols=(), calls_per_minute=30)


def test_valid_file(tmp_path):
    config = load_config(_write(tmp_path, "seed_symbols: [AAPL, BRK.B]\ncalls_per_minute: 60\n"))
    assert config.seed_symbols == ("AAPL", "BRK.B")
    assert config.calls_per_minute == 60


@pytest.mark.parametrize(
    "text",
    [
        "calls_per_minute: 30\n",  # missing key
        "seed_symbols: []\n",  # missing key
        "seed_symbols: []\ncalls_per_minute: 30\nextra: 1\n",  # unknown key
        "seed_symbols: AAPL\ncalls_per_minute: 30\n",  # not a list
        "seed_symbols: [1]\ncalls_per_minute: 30\n",  # not strings
        "seed_symbols: [aapl]\ncalls_per_minute: 30\n",  # fails the ticker check
        "seed_symbols: [AAPL, AAPL]\ncalls_per_minute: 30\n",  # duplicate
        "seed_symbols: []\ncalls_per_minute: 0\n",
        "seed_symbols: []\ncalls_per_minute: 301\n",
        "seed_symbols: []\ncalls_per_minute: 30.5\n",
        "seed_symbols: []\ncalls_per_minute: true\n",
        "- not a mapping\n",
        "seed_symbols: [\n",  # unparseable
    ],
)
def test_bad_files_are_rejected(tmp_path, text):
    with pytest.raises(ReferenceConfigError):
        load_config(_write(tmp_path, text))


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ReferenceConfigError):
        load_config(tmp_path / "absent.yaml")


@pytest.mark.parametrize("boundary", [1, 300])
def test_rate_boundaries_are_allowed(tmp_path, boundary):
    text = f"seed_symbols: []\ncalls_per_minute: {boundary}\n"
    assert load_config(_write(tmp_path, text)).calls_per_minute == boundary
