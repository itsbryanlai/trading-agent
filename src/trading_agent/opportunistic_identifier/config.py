"""Load and validate config/opportunistic_identifier.yaml (contracts/oi-interface.md).

Strict, like config/research.yaml: every key required, no unknown keys, exact types and
ranges. On top of the per-key bounds, a normally paced slice must be fetchable inside the
run's fetch window (research O10). `config/risk.yaml` is loaded with the Risk Gate's own
loader, and only its `universe` is kept (research O4). A bad file means the agent refuses
to start. Error messages name the key and never a credential.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path

import yaml

from trading_agent.llm.settings import (
    ModelSettings,
    ModelSettingsError,
    parse_model_settings,
    provider_variables,
)
from trading_agent.reference.symbols import is_plausible_ticker
from trading_agent.risk.config import RiskConfigError, UniverseConfig
from trading_agent.risk.config import load_config as load_risk_config

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
DEFAULT_CONFIG_PATH = _CONFIG_DIR / "opportunistic_identifier.yaml"
DEFAULT_RISK_PATH = _CONFIG_DIR / "risk.yaml"

# config/schedule.yaml's opportunistic_identifier.timeout_minutes x 60; a test keeps them equal.
RUN_BUDGET_SECONDS = 900
# Headroom under the orchestrator's timeout for what can't be bounded exactly: start-up, the
# database connect, and the Anthropic SDK's wait before its retry.
RUN_MARGIN_SECONDS = 60
# The symbol list, screening and the write.
RUN_SLACK_SECONDS = 60
# Finnhub's per-call socket timeout; a test keeps it equal to finnhub.TIMEOUT_SECONDS.
FINNHUB_CALL_TIMEOUT_SECONDS = 10
# Model requests one run can make: Qwen makes one; the Anthropic SDK retries once (a test
# keeps this equal to llm.anthropic_client.MAX_RETRIES + 1).
MODEL_ATTEMPTS = {"qwen": 1, "anthropic": 2}
# The symbol list is one Finnhub call per exchange; each name costs three.
SYMBOL_LIST_CALLS = 3
CALLS_PER_NAME = 3
MAX_SCAN_UNIVERSE = 1000

VARIABLE_PREFIX = "OPPORTUNISTIC_IDENTIFIER_"
MODEL_TIMEOUT_BOUNDS = (30, 300)

_INTS = {
    "slice_size": (1, 200),
    "shortlist_size": (1, 40),
    "quote_max_age_minutes": (1, 60),
    "finnhub_calls_per_minute": (1, 60),
    "rationale_max_chars": (200, 10_000),
    "max_input_chars": (5_000, 300_000),
}
_KEYS = {"scan_universe", "slots", "model", *_INTS}
_SLOT_KEYS = {"first", "last", "every_minutes", "before_close_minutes"}
_SLOT_INTS = {"every_minutes": (15, 240), "before_close_minutes": (0, 120)}
_MODEL_KEYS = {"provider", "name", "anthropic_effort", "max_output_tokens", "timeout_seconds"}
_HH_MM = re.compile(r"(\d{2}):(\d{2})")


class OIConfigError(Exception):
    pass


@dataclass(frozen=True)
class Slots:
    """The orchestrator's slot rule (config/schedule.yaml), copied here so the agent can
    name its own slot without importing the orchestrator (research O3)."""

    first: time
    last: time
    every_minutes: int
    before_close_minutes: int


@dataclass(frozen=True)
class OIConfig:
    scan_universe: tuple[str, ...]
    slice_size: int
    shortlist_size: int
    quote_max_age_minutes: int
    finnhub_calls_per_minute: int
    rationale_max_chars: int
    max_input_chars: int
    slots: Slots
    model: ModelSettings
    universe: UniverseConfig

    @property
    def provider_key_variable(self) -> str:
        return provider_variables(VARIABLE_PREFIX, self.model.provider)[0]

    @property
    def fetch_window_seconds(self) -> int:
        """How long fetching may run: the run budget less margin, slack, every model
        attempt and one last Finnhub call that starts just before the deadline."""
        model = MODEL_ATTEMPTS[self.model.provider] * self.model.timeout_seconds
        return (
            RUN_BUDGET_SECONDS
            - RUN_MARGIN_SECONDS
            - RUN_SLACK_SECONDS
            - model
            - FINNHUB_CALL_TIMEOUT_SECONDS
        )

    def fetch_deadline(self, start: float) -> float:
        """No Finnhub call starts after this (`start` and the result are on one clock)."""
        return start + self.fetch_window_seconds

    @property
    def paced_fetch_seconds(self) -> float:
        """A normally paced slice: the symbol list and three calls a name, at the pace."""
        calls = SYMBOL_LIST_CALLS + CALLS_PER_NAME * self.slice_size
        return calls * 60 / self.finnhub_calls_per_minute


def load_config(path: Path = DEFAULT_CONFIG_PATH, risk_path: Path = DEFAULT_RISK_PATH) -> OIConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise OIConfigError(f"{path}: cannot read: {type(exc).__name__}") from exc
    _keys(data, _KEYS, "")
    ints = {key: _int(data[key], key, *bounds) for key, bounds in _INTS.items()}
    if ints["shortlist_size"] > ints["slice_size"]:
        raise OIConfigError("shortlist_size: must not exceed slice_size")
    try:
        settings = _model(data["model"])
        universe = load_risk_config(Path(risk_path)).universe
    except RiskConfigError as exc:
        raise OIConfigError(f"risk config {risk_path}: {exc}") from exc

    config = OIConfig(
        scan_universe=_scan_universe(data["scan_universe"]),
        slots=_slots(data["slots"]),
        model=settings,
        universe=universe,
        **ints,
    )
    # Each rationale is at most rationale_max_chars (about 3 characters a token, plus about 60
    # tokens for the rest of its proposal), and all of them must fit the model's output, or
    # the answer is cut off and discarded (review M4).
    if config.shortlist_size * (config.rationale_max_chars + 180) > 3 * settings.max_output_tokens:
        raise OIConfigError(
            f"shortlist_size x rationale_max_chars: {config.shortlist_size} proposals of up to "
            f"{config.rationale_max_chars} characters need about "
            f"{config.shortlist_size * (config.rationale_max_chars + 180) // 3} output tokens, "
            f"more than model.max_output_tokens ({settings.max_output_tokens}): lower "
            "shortlist_size or rationale_max_chars, or raise model.max_output_tokens"
        )
    if config.paced_fetch_seconds > config.fetch_window_seconds:
        raise OIConfigError(
            f"budget: a paced slice of {config.slice_size} names takes "
            f"{config.paced_fetch_seconds:.0f} s but the fetch window is "
            f"{config.fetch_window_seconds} s ({RUN_BUDGET_SECONDS} s run budget less "
            f"{RUN_MARGIN_SECONDS} s margin, {RUN_SLACK_SECONDS} s slack, "
            f"{MODEL_ATTEMPTS[settings.provider]} model attempt(s) of "
            f"{settings.timeout_seconds} s and {FINNHUB_CALL_TIMEOUT_SECONDS} s for one call; "
            "research O10): lower slice_size or model.timeout_seconds, or raise "
            "finnhub_calls_per_minute"
        )
    return config


def _model(section) -> ModelSettings:
    _keys(section, _MODEL_KEYS, "model.")
    try:
        return parse_model_settings(section, timeout_bounds=MODEL_TIMEOUT_BOUNDS)
    except ModelSettingsError as exc:
        raise OIConfigError(str(exc)) from exc


def _keys(data, expected: set[str], prefix: str) -> None:
    if not isinstance(data, dict):
        raise OIConfigError(f"{prefix.rstrip('.') or 'config'}: must be a mapping")
    for key in sorted(expected - data.keys()):
        raise OIConfigError(f"{prefix}{key}: required setting is missing")
    for key in sorted(set(data) - expected, key=str):
        raise OIConfigError(f"{prefix}{key}: unknown setting")


def _int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OIConfigError(f"{name}: must be an integer, got {value!r}")
    if not low <= value <= high:
        raise OIConfigError(f"{name}: {value} is outside {low}-{high}")
    return value


def _slots(section) -> Slots:
    _keys(section, _SLOT_KEYS, "slots.")
    ints = {key: _int(section[key], f"slots.{key}", *b) for key, b in _SLOT_INTS.items()}
    first, last = _hh_mm(section["first"], "slots.first"), _hh_mm(section["last"], "slots.last")
    if first > last:
        raise OIConfigError("slots.first: must not be after slots.last")
    return Slots(first=first, last=last, **ints)


def _hh_mm(value, name: str) -> time:
    match = _HH_MM.fullmatch(value) if isinstance(value, str) else None
    if match is None or int(match[1]) > 23 or int(match[2]) > 59:
        raise OIConfigError(f"{name}: must be HH:MM, got {value!r}")
    return time(int(match[1]), int(match[2]))


def _scan_universe(value) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise OIConfigError("scan_universe: must be a list of tickers")
    for symbol in value:
        if not is_plausible_ticker(symbol):
            raise OIConfigError(f"scan_universe: {symbol!r} is not a US ticker")
        if "." in symbol or "-" in symbol:
            # A share-class ticker could never pass eligibility (reference.normalize).
            raise OIConfigError(f"scan_universe: {symbol!r} is a share-class ticker")
    unique = sorted(set(value))
    if len(unique) > MAX_SCAN_UNIVERSE:
        raise OIConfigError(f"scan_universe: at most {MAX_SCAN_UNIVERSE} symbols")
    return tuple(unique)
