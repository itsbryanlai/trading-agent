"""config/opportunistic_identifier.yaml is strict and bounded, and its budget check keeps a
normally paced slice inside the run's fetch window (specs/011-opportunistic-identifier
research O3, O10; contracts/oi-interface.md "Configuration")."""

from __future__ import annotations

import copy
from datetime import time
from pathlib import Path

import pytest
import yaml

from trading_agent.llm import anthropic_client
from trading_agent.opportunistic_identifier import config as c
from trading_agent.opportunistic_identifier import finnhub
from trading_agent.opportunistic_identifier.config import OIConfigError, load_config
from trading_agent.risk import config as risk_config

ROOT = Path(__file__).resolve().parents[3]
RISK = ROOT / "config" / "risk.yaml"
SCHEDULE = ROOT / "config" / "schedule.yaml"

# The loader's rules start from this fixed config (an empty scan list and the shipped
# settings), not from config/opportunistic_identifier.yaml: the owner edits that file's
# scan list, and no test but the shipped-file ones may care.
BASE = {
    "scan_universe": [],
    "slice_size": 40,
    "shortlist_size": 20,
    "quote_max_age_minutes": 15,
    "finnhub_calls_per_minute": 20,
    "rationale_max_chars": 2000,
    "max_input_chars": 60000,
    "slots": {"first": "10:00", "last": "15:00", "every_minutes": 60, "before_close_minutes": 30},
    "model": {
        "provider": "qwen",
        "name": "qwen3.7-plus",
        "max_output_tokens": 8000,
        "timeout_seconds": 120,
        "anthropic_effort": "medium",
    },
}


def base() -> dict:
    return copy.deepcopy(BASE)


def changed(*edits) -> dict:
    """base() with each (path, value) applied."""
    data = base()
    for path, value in edits:
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return data


def load(tmp_path, data, risk_path=RISK):
    path = tmp_path / "oi.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_config(path, risk_path)


def anthropic(*edits) -> dict:
    return changed(
        (("model", "provider"), "anthropic"), (("model", "name"), "claude-sonnet-5-5"), *edits
    )


# --- the shipped file ----------------------------------------------------------------------


def test_shipped_file_loads():
    cfg = load_config(c.DEFAULT_CONFIG_PATH, c.DEFAULT_RISK_PATH)
    assert isinstance(cfg.scan_universe, tuple)  # its contents are the owner's to change
    assert (cfg.slice_size, cfg.shortlist_size, cfg.quote_max_age_minutes) == (40, 20, 15)
    assert cfg.finnhub_calls_per_minute == 20
    assert cfg.model.provider == "qwen" and cfg.model.name == "qwen3.7-plus"
    assert cfg.model.timeout_seconds == 120
    assert cfg.slots == c.Slots(time(10, 0), time(15, 0), 60, 30)
    assert cfg.provider_key_variable == "OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY"


def test_the_shipped_file_uses_about_369_seconds_of_a_650_second_window():
    cfg = load_config(c.DEFAULT_CONFIG_PATH, c.DEFAULT_RISK_PATH)
    assert cfg.fetch_window_seconds == 650
    assert (3 + 3 * cfg.slice_size) * 60 / cfg.finnhub_calls_per_minute == 369


def test_anthropic_names_its_own_key_variable(tmp_path):
    cfg = load(tmp_path, anthropic())
    assert cfg.provider_key_variable == "OPPORTUNISTIC_IDENTIFIER_ANTHROPIC_API_KEY"


# --- keys ----------------------------------------------------------------------------------


def test_unreadable_or_not_a_mapping(tmp_path):
    with pytest.raises(OIConfigError):
        load_config(tmp_path / "missing.yaml", RISK)
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n")
    with pytest.raises(OIConfigError, match="mapping"):
        load_config(path, RISK)


@pytest.mark.parametrize("key", list(BASE))
def test_every_top_level_key_is_required(tmp_path, key):
    data = base()
    del data[key]
    with pytest.raises(OIConfigError, match=key):
        load(tmp_path, data)


@pytest.mark.parametrize("key", list(BASE["slots"]))
def test_every_slots_key_is_required(tmp_path, key):
    data = base()
    del data["slots"][key]
    with pytest.raises(OIConfigError, match=key):
        load(tmp_path, data)


@pytest.mark.parametrize("key", list(BASE["model"]))
def test_every_model_key_is_required(tmp_path, key):
    data = base()
    del data["model"][key]
    with pytest.raises(OIConfigError, match=key):
        load(tmp_path, data)


@pytest.mark.parametrize("where", [(), ("slots",), ("model",)])
def test_unknown_keys_are_rejected_at_every_level(tmp_path, where):
    data = base()
    target = data
    for key in where:
        target = target[key]
    target["surprise"] = 1
    with pytest.raises(OIConfigError, match="surprise"):
        load(tmp_path, data)


# --- bounds --------------------------------------------------------------------------------

# (path, low, high, edits that make both ends valid together with the other settings)
BOUNDS = [
    (
        ("slice_size",),
        1,
        200,
        {1: [(("shortlist_size",), 1)], 200: [(("finnhub_calls_per_minute",), 60)]},
    ),
    (
        ("shortlist_size",),
        1,
        40,
        {41: [(("slice_size",), 60), (("finnhub_calls_per_minute",), 60)]},
    ),
    (("quote_max_age_minutes",), 1, 60, {}),
    (("finnhub_calls_per_minute",), 1, 60, {1: [(("slice_size",), 1), (("shortlist_size",), 1)]}),
    (("rationale_max_chars",), 200, 10000, {}),
    (("max_input_chars",), 5000, 300000, {}),
    (("slots", "every_minutes"), 15, 240, {}),
    (("slots", "before_close_minutes"), 0, 120, {}),
    (("model", "timeout_seconds"), 30, 300, {}),
]


@pytest.mark.parametrize(("path", "low", "high", "extra"), BOUNDS, ids=[b[0][-1] for b in BOUNDS])
def test_integer_bounds(tmp_path, path, low, high, extra):
    for edge in (low, high):
        load(tmp_path, changed((path, edge), *extra.get(edge, [])))
    for bad in (low - 1, high + 1):
        with pytest.raises(OIConfigError, match=path[-1]):
            load(tmp_path, changed((path, bad), *extra.get(bad, [])))


@pytest.mark.parametrize(("path", "low", "high", "extra"), BOUNDS, ids=[b[0][-1] for b in BOUNDS])
def test_booleans_strings_and_floats_are_not_integers(tmp_path, path, low, high, extra):
    for bad in (True, str(low), float(low) + 0.5):
        with pytest.raises(OIConfigError, match=path[-1]):
            load(tmp_path, changed((path, bad)))


def test_the_shortlist_cannot_exceed_the_slice(tmp_path):
    load(tmp_path, changed((("slice_size",), 20), (("shortlist_size",), 20)))
    with pytest.raises(OIConfigError, match="shortlist_size"):
        load(tmp_path, changed((("slice_size",), 20), (("shortlist_size",), 21)))


@pytest.mark.parametrize("bad", ["9:00", "25:00", "10:60", "1000", "", 10, None, "10:00:00"])
def test_slot_times_are_hh_mm(tmp_path, bad):
    for key in ("first", "last"):
        with pytest.raises(OIConfigError, match=key):
            load(tmp_path, changed((("slots", key), bad)))


def test_the_first_slot_cannot_be_after_the_last(tmp_path):
    load(tmp_path, changed((("slots", "first"), "15:00")))
    with pytest.raises(OIConfigError, match="first"):
        load(tmp_path, changed((("slots", "first"), "15:01")))


# --- the scan universe ---------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["aapl", "TOOLONG", "BRK/B", "", 5, None, "BRK.B", "BF-B"])
def test_scan_entries_must_be_plain_tickers(tmp_path, bad):
    with pytest.raises(OIConfigError, match="scan_universe"):
        load(tmp_path, changed((("scan_universe",), ["AAPL", bad])))


def test_scan_universe_must_be_a_list(tmp_path):
    for bad in ("AAPL", {"AAPL": 1}, None):
        with pytest.raises(OIConfigError, match="scan_universe"):
            load(tmp_path, changed((("scan_universe",), bad)))


def test_scan_universe_is_deduplicated_and_sorted(tmp_path):
    cfg = load(tmp_path, changed((("scan_universe",), ["MSFT", "AAPL", "MSFT", "NVDA", "AAPL"])))
    assert cfg.scan_universe == ("AAPL", "MSFT", "NVDA")


def test_scan_universe_has_at_most_1000_names(tmp_path):
    names = [f"{a}{b}{d}" for a in "ABCDEFGHIJ" for b in "ABCDEFGHIJ" for d in "ABCDEFGHIJ"]
    assert len(names) == 1000
    assert len(load(tmp_path, changed((("scan_universe",), names))).scan_universe) == 1000
    with pytest.raises(OIConfigError, match="1000"):
        load(tmp_path, changed((("scan_universe",), [*names, "ZZZ"])))


# --- the budget (research O10) ---------------------------------------------------------------


def test_the_constants_the_budget_is_built_from():
    assert c.RUN_BUDGET_SECONDS == 900
    assert (c.RUN_MARGIN_SECONDS, c.RUN_SLACK_SECONDS) == (60, 60)
    assert c.MODEL_ATTEMPTS == {"qwen": 1, "anthropic": 2}


def test_the_anthropic_attempts_are_the_sdk_retries_plus_one():
    assert c.MODEL_ATTEMPTS["anthropic"] == anthropic_client.MAX_RETRIES + 1


def test_the_finnhub_call_timeout_is_the_adapters():
    assert c.FINNHUB_CALL_TIMEOUT_SECONDS == finnhub.TIMEOUT_SECONDS


def test_the_largest_slice_on_qwen_at_20_calls_a_minute_is_71(tmp_path):
    load(tmp_path, changed((("slice_size",), 71)))
    with pytest.raises(OIConfigError, match="budget"):
        load(tmp_path, changed((("slice_size",), 72)))


def test_the_largest_slice_on_anthropic_at_20_calls_a_minute_is_57(tmp_path):
    load(tmp_path, anthropic((("slice_size",), 57)))
    with pytest.raises(OIConfigError, match="budget"):
        load(tmp_path, anthropic((("slice_size",), 58)))


def test_a_longer_model_timeout_shrinks_the_window(tmp_path):
    with pytest.raises(OIConfigError, match="budget"):
        load(tmp_path, changed((("slice_size",), 71), (("model", "timeout_seconds"), 130)))


def test_a_faster_pace_allows_a_larger_slice(tmp_path):
    load(tmp_path, changed((("slice_size",), 200), (("finnhub_calls_per_minute",), 60)))


def test_the_window_is_the_budget_less_margin_slack_model_attempts_and_one_call(tmp_path):
    assert load(tmp_path, base()).fetch_window_seconds == 900 - 60 - 60 - 120 - 10
    assert load(tmp_path, anthropic()).fetch_window_seconds == 900 - 60 - 60 - 240 - 10


def test_the_fetch_deadline_is_the_start_plus_the_window(tmp_path):
    cfg = load(tmp_path, base())
    assert cfg.fetch_deadline(1000.0) == 1650.0
    assert load(tmp_path, anthropic()).fetch_deadline(0.0) == 530.0


# --- the risk file's universe ------------------------------------------------------------------


def test_only_the_universe_of_risk_yaml_is_kept(tmp_path):
    cfg = load(tmp_path, base())
    assert cfg.universe == risk_config.load_config(RISK).universe
    assert not hasattr(cfg, "max_position_pct")


def test_a_risk_file_that_does_not_load_refuses_the_start(tmp_path):
    bad = tmp_path / "risk.yaml"
    bad.write_text("max_position_pct: 8\n")
    with pytest.raises(OIConfigError, match="risk"):
        load(tmp_path, base(), risk_path=bad)
    with pytest.raises(OIConfigError, match="risk"):
        load(tmp_path, base(), risk_path=tmp_path / "missing.yaml")


# --- the schedule guard (research O3, O10) -------------------------------------------------------


def _schedule() -> dict:
    return yaml.safe_load(SCHEDULE.read_text())


def test_the_run_budget_is_the_schedules_timeout():
    assert c.RUN_BUDGET_SECONDS == 60 * _schedule()["opportunistic_identifier"]["timeout_minutes"]


def test_the_slots_match_the_orchestrators_schedule():
    schedule, slots = _schedule(), load_config(c.DEFAULT_CONFIG_PATH, RISK).slots
    oi = schedule["opportunistic_identifier"]

    def hh_mm(t: time) -> str:
        return f"{t.hour:02d}:{t.minute:02d}"

    assert hh_mm(slots.first) == oi["window_start"]
    assert hh_mm(slots.last) == oi["window_end"]
    assert slots.every_minutes == oi["interval_minutes"]
    assert slots.before_close_minutes == schedule["portfolio_manager"]["before_close_minutes"]


def test_a_slice_that_exactly_fills_the_window_passes_and_one_second_less_fails(tmp_path):
    # 200 names at 60 calls a minute: (3 + 600) calls = 603 s. Window = 770 - model timeout.
    exact = changed(
        (("slice_size",), 200),
        (("finnhub_calls_per_minute",), 60),
        (("model", "timeout_seconds"), 167),
    )
    assert load(tmp_path, exact).fetch_window_seconds == 603
    exact["model"]["timeout_seconds"] = 168
    with pytest.raises(OIConfigError, match="budget"):
        load(tmp_path, exact)
