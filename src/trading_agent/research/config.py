"""Load and validate config/research.yaml (contracts/research-interface.md).

Strict, like reference_data.yaml: every key required, no unknown keys, exact types
and ranges. On top of the per-key bounds, the worst-case run time must fit the
orchestrator's timeout for Research (research R11). A bad file means Research
refuses to start.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from trading_agent.llm.settings import (
    ModelSettings,
    ModelSettingsError,
    parse_model_settings,
    provider_variables,
)
from trading_agent.reference.symbols import is_plausible_ticker

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "research.yaml"

# config/schedule.yaml's research.timeout_minutes × 60; a test keeps them equal.
RUN_BUDGET_SECONDS = 900
# Finnhub's per-call socket timeout (research/finnhub.py), counted in the budget.
NEWS_CALL_TIMEOUT_SECONDS = 10
# The symbol list is one Finnhub request per exchange (research/finnhub.py
# SYMBOL_LIST_MICS); a test keeps the two equal.
SYMBOL_LIST_CALLS = 3
# Selection, the write and start-up.
RUN_SLACK_SECONDS = 60
# Headroom under the orchestrator's timeout for what can't be bounded exactly: per-read
# socket timeouts that a trickling response stretches, the Anthropic SDK's wait before
# its retry, the database connect (review M3).
RUN_MARGIN_SECONDS = 60
MAX_WATCHLIST = 50

VARIABLE_PREFIX = "RESEARCH_"
MODEL_TIMEOUT_BOUNDS = (30, 360)

_INTS = {
    "general_news_max_articles": (0, 100),
    "articles_per_symbol": (1, 20),
    "article_summary_max_chars": (100, 5000),
    "max_input_chars": (10_000, 2_000_000),
    "rationale_max_chars": (200, 10_000),
    "finnhub_calls_per_minute": (1, 300),
}
_KEYS = {"watchlist", "model", *_INTS}
_MODEL_KEYS = {"provider", "name", "anthropic_effort", "max_output_tokens", "timeout_seconds"}


class ResearchConfigError(Exception):
    pass


@dataclass(frozen=True)
class ResearchConfig:
    watchlist: tuple[str, ...]
    general_news_max_articles: int
    articles_per_symbol: int
    article_summary_max_chars: int
    max_input_chars: int
    rationale_max_chars: int
    finnhub_calls_per_minute: int
    model: ModelSettings

    @property
    def provider_key_variable(self) -> str:
        return provider_variables(VARIABLE_PREFIX, self.model.provider)[0]

    @property
    def news_budget_seconds(self) -> float:
        """The fetch phase's deadline: every call paced, each allowed its timeout (R3).

        The calls are the symbol list, the general feed and one per watchlist symbol."""
        per_call = 60 / self.finnhub_calls_per_minute + NEWS_CALL_TIMEOUT_SECONDS
        return (len(self.watchlist) + 1 + SYMBOL_LIST_CALLS) * per_call

    @property
    def worst_case_seconds(self) -> float:
        return 2 * self.model.timeout_seconds + self.news_budget_seconds + RUN_SLACK_SECONDS


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> ResearchConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ResearchConfigError(f"{path}: cannot read: {exc}") from exc
    _keys(data, _KEYS, "")
    model = data["model"]
    _keys(model, _MODEL_KEYS, "model.")

    ints = {key: _int(data[key], key, *bounds) for key, bounds in _INTS.items()}
    try:
        settings = parse_model_settings(model, timeout_bounds=MODEL_TIMEOUT_BOUNDS)
    except ModelSettingsError as exc:
        raise ResearchConfigError(str(exc)) from exc

    config = ResearchConfig(
        watchlist=_watchlist(data["watchlist"]),
        model=settings,
        **ints,
    )
    if config.worst_case_seconds > RUN_BUDGET_SECONDS - RUN_MARGIN_SECONDS:
        raise ResearchConfigError(
            f"worst-case run time {config.worst_case_seconds:.0f} s exceeds the "
            f"{RUN_BUDGET_SECONDS - RUN_MARGIN_SECONDS} s run budget ({RUN_BUDGET_SECONDS} s less "
            f"{RUN_MARGIN_SECONDS} s margin; research R11): lower model.timeout_seconds "
            "or the watchlist, or raise finnhub_calls_per_minute"
        )
    return config


def _keys(data, expected: set[str], prefix: str) -> None:
    if not isinstance(data, dict):
        raise ResearchConfigError(f"{prefix or 'config'}: must be a mapping")
    for key in sorted(expected - data.keys()):
        raise ResearchConfigError(f"{prefix}{key}: required setting is missing")
    for key in sorted(set(data) - expected, key=str):
        raise ResearchConfigError(f"{prefix}{key}: unknown setting")


def _int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchConfigError(f"{name}: must be an integer, got {value!r}")
    if not low <= value <= high:
        raise ResearchConfigError(f"{name}: {value} is outside {low}–{high}")
    return value


def _watchlist(value) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ResearchConfigError("watchlist: must be a list of tickers")
    for symbol in value:
        if not is_plausible_ticker(symbol):
            raise ResearchConfigError(f"watchlist: {symbol!r} is not a US ticker")
    if len(set(value)) != len(value):
        raise ResearchConfigError("watchlist: duplicate symbols")
    if len(value) > MAX_WATCHLIST:
        raise ResearchConfigError(f"watchlist: at most {MAX_WATCHLIST} symbols")
    return tuple(value)
