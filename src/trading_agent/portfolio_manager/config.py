"""Load and validate config/portfolio_manager.yaml (specs/008-portfolio-manager research
P11; contracts/pm-interface.md "Configuration").

Strict, like research.yaml: every key required, no unknown keys, exact types and ranges.
A bad file means the PM refuses to start (exit 2). The model section is parsed by
`trading_agent.llm.settings`, shared with Research.

Seam for User Story 5: the two cross-checks on worst-case run time (against
the orchestrator's timeout and the gate's staleness limit) are added here.
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

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "portfolio_manager.yaml"
VARIABLE_PREFIX = "PORTFOLIO_MANAGER_"
MODEL_TIMEOUT_BOUNDS = (30, 240)

_INTS = {
    "quote_max_age_minutes": (1, 10),
    "finnhub_calls_per_minute": (1, 300),
    "quote_phase_seconds": (10, 300),
    "journal_entries": (0, 20),
    "journal_summary_max_chars": (200, 10_000),
    "rationale_max_chars": (200, 10_000),
    "reasoning_max_chars": (200, 10_000),
    "max_input_chars": (10_000, 2_000_000),
}
_KEYS = {"model", *_INTS}


class PortfolioManagerConfigError(Exception):
    pass


@dataclass(frozen=True)
class PortfolioManagerConfig:
    quote_max_age_minutes: int
    finnhub_calls_per_minute: int
    quote_phase_seconds: int
    journal_entries: int
    journal_summary_max_chars: int
    rationale_max_chars: int
    reasoning_max_chars: int
    max_input_chars: int
    model: ModelSettings

    @property
    def provider_variables(self) -> tuple[str, ...]:
        """The provider's API key variable first, then (for Qwen) its endpoint."""
        return provider_variables(VARIABLE_PREFIX, self.model.provider)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> PortfolioManagerConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise PortfolioManagerConfigError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(data, dict):
        raise PortfolioManagerConfigError("config: must be a mapping")
    for key in sorted(_KEYS - data.keys()):
        raise PortfolioManagerConfigError(f"{key}: required setting is missing")
    for key in sorted(set(data) - _KEYS, key=str):
        raise PortfolioManagerConfigError(f"{key}: unknown setting")

    ints = {key: _int(data[key], key, *bounds) for key, bounds in _INTS.items()}
    try:
        model = parse_model_settings(data["model"], timeout_bounds=MODEL_TIMEOUT_BOUNDS)
    except ModelSettingsError as exc:
        raise PortfolioManagerConfigError(str(exc)) from exc
    return PortfolioManagerConfig(model=model, **ints)


def _int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PortfolioManagerConfigError(f"{name}: must be an integer, got {value!r}")
    if not low <= value <= high:
        raise PortfolioManagerConfigError(f"{name}: {value} is outside {low}–{high}")
    return value
