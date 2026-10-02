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
    ResearchConfigError,
    load_config,
)

TICKERS = [f"{a}{b}" for a in "ABCDEFGHIJ" for b in "KLMNOP"]


def test_the_budget_matches_the_orchestrators_timeout():
    schedule = yaml.safe_load(SCHEDULE_PATH.read_text())
    assert RUN_BUDGET_SECONDS == schedule["research"]["timeout_minutes"] * 60 == 900


def test_the_shipped_config_fits():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.worst_case_seconds == 2 * 180 + 2 * (2 + 10) + 60 == 444
    assert cfg.worst_case_seconds <= RUN_BUDGET_SECONDS


def test_over_budget_is_refused(tmp_path):
    data = changed(("model", "timeout_seconds"), 360)
    data["watchlist"] = TICKERS[:50]  # 720 + 52 × 12 + 60 = 1404 s
    with pytest.raises(ResearchConfigError, match="840 s run budget"):
        load(tmp_path, data)


def test_exactly_at_the_budget_is_accepted(tmp_path):
    # 2 × 300 + (N + 2) × (2 + 10) + 60 = 840 (900 less the 60 s margin)  →  N = 13
    data = changed(("model", "timeout_seconds"), 300)
    data["watchlist"] = TICKERS[:13]
    assert load(tmp_path, data).worst_case_seconds == 840
    data["watchlist"] = TICKERS[:14]
    with pytest.raises(ResearchConfigError, match="run budget"):
        load(tmp_path, data)


def test_the_default_timeout_allows_a_watchlist_of_33(tmp_path):
    data = changed(("watchlist",), TICKERS[:33])
    assert load(tmp_path, data).worst_case_seconds <= RUN_BUDGET_SECONDS - 60
    data["watchlist"] = TICKERS[:34]
    with pytest.raises(ResearchConfigError, match="run budget"):
        load(tmp_path, data)


def test_the_news_deadline_is_the_fetch_phase_term():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.news_budget_seconds == (0 + 2) * (60 / 30 + 10)
