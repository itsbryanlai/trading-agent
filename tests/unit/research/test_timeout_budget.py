"""The worst-case run fits the orchestrator's timeout for Research (specs/007-research-agent
research R11; analyze T1)."""

from __future__ import annotations

import pytest
import yaml

from tests.unit.research.test_config import changed, load
from trading_agent.orchestrator.config import DEFAULT_CONFIG_PATH as SCHEDULE_PATH
from trading_agent.research.config import (
    DEFAULT_CONFIG_PATH,
    RUN_BUDGET_SECONDS,
    RUN_MARGIN_SECONDS,
    SYMBOL_LIST_CALLS,
    ResearchConfigError,
    load_config,
)
from trading_agent.research.finnhub import SYMBOL_LIST_MICS

TICKERS = [f"{a}{b}" for a in "ABCDEFGHIJ" for b in "KLMNOP"]


def test_the_budget_matches_the_orchestrators_timeout():
    schedule = yaml.safe_load(SCHEDULE_PATH.read_text())
    assert RUN_BUDGET_SECONDS == schedule["research"]["timeout_minutes"] * 60 == 900


def test_the_default_settings_with_no_watchlist_take_468_seconds(tmp_path):
    cfg = load(tmp_path, changed(("watchlist",), []))
    assert cfg.worst_case_seconds == 2 * 180 + 4 * (2 + 10) + 60 == 468
    assert cfg.worst_case_seconds <= RUN_BUDGET_SECONDS


def test_the_shipped_config_fits():
    # Whatever the owner's watchlist is, the shipped file must load, and the loader refuses
    # a file over the budget; this checks the same bound from outside.
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.worst_case_seconds <= RUN_BUDGET_SECONDS - RUN_MARGIN_SECONDS


def test_over_budget_is_refused(tmp_path):
    data = changed(("model", "timeout_seconds"), 360)
    data["watchlist"] = TICKERS[:50]  # 720 + 54 × 12 + 60 = 1428 s
    with pytest.raises(ResearchConfigError, match="840 s run budget"):
        load(tmp_path, data)


def test_exactly_at_the_budget_is_accepted(tmp_path):
    # 2 × 300 + (N + 4) × (2 + 10) + 60 = 840 (900 less the 60 s margin)  →  N = 11
    data = changed(("model", "timeout_seconds"), 300)
    data["watchlist"] = TICKERS[:11]
    assert load(tmp_path, data).worst_case_seconds == 840
    data["watchlist"] = TICKERS[:12]
    with pytest.raises(ResearchConfigError, match="run budget"):
        load(tmp_path, data)


def test_the_default_timeout_allows_a_watchlist_of_31(tmp_path):
    data = changed(("watchlist",), TICKERS[:31])
    assert load(tmp_path, data).worst_case_seconds <= RUN_BUDGET_SECONDS - 60
    data["watchlist"] = TICKERS[:32]
    with pytest.raises(ResearchConfigError, match="run budget"):
        load(tmp_path, data)


def test_the_news_deadline_is_the_fetch_phase_term(tmp_path):
    cfg = load(tmp_path, changed(("watchlist",), []))
    # the general feed, the symbol list (one call per exchange) and the watchlist
    assert cfg.news_budget_seconds == (0 + 1 + 3) * (60 / 30 + 10)


def test_the_budget_counts_one_symbol_list_call_per_exchange():
    assert SYMBOL_LIST_CALLS == len(SYMBOL_LIST_MICS)
