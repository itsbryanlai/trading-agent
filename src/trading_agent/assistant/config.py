"""Load and validate config/assistant.yaml (contracts/assistant-interface.md).

Strict, like the other components' files: every key required, no unknown keys, integers
(never booleans) within bounds. A bad file means the service refuses to start. Error messages
name the key and never a credential.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from trading_agent.llm.settings import (
    ModelSettings,
    ModelSettingsError,
    parse_model_settings,
)
from trading_agent.llm.settings import provider_variables as _provider_variables

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "assistant.yaml"
VARIABLE_PREFIX = "ASSISTANT_"
MODEL_TIMEOUT_BOUNDS = (10, 120)
ACTION_WORDS_BOUNDS = (1, 30)

_SECTIONS = {
    "memory": {"exchanges": (0, 20), "idle_minutes": (1, 240)},
    "limits": {
        "max_queries_per_question": (1, 10),
        "max_rows_per_query": (1, 500),
        "max_span_days": (1, 366),
        "max_input_chars": (5000, 400_000),
        "text_field_chars": (100, 10_000),
        "max_reply_chars": (500, 40_000),
    },
    "polling": {"long_poll_seconds": (10, 55)},
    "database": {"statement_timeout_seconds": (1, 60), "max_consecutive_failures": (1, 20)},
}
_KEYS = {"owner_time_zone", "model", "action_words", *_SECTIONS}
_MODEL_KEYS = {"provider", "name", "max_output_tokens", "timeout_seconds", "anthropic_effort"}


class AssistantConfigError(Exception):
    pass


@dataclass(frozen=True)
class MemoryConfig:
    exchanges: int
    idle_minutes: int


@dataclass(frozen=True)
class LimitsConfig:
    max_queries_per_question: int
    max_rows_per_query: int
    max_span_days: int
    max_input_chars: int
    text_field_chars: int
    max_reply_chars: int


@dataclass(frozen=True)
class PollingConfig:
    long_poll_seconds: int


@dataclass(frozen=True)
class DatabaseConfig:
    statement_timeout_seconds: int
    max_consecutive_failures: int


@dataclass(frozen=True)
class AssistantConfig:
    owner_time_zone: str
    model: ModelSettings
    action_words: tuple[str, ...]
    memory: MemoryConfig
    limits: LimitsConfig
    polling: PollingConfig
    database: DatabaseConfig


def provider_variables(provider: str) -> tuple[str, ...]:
    """The environment variables the provider needs, under the ASSISTANT_ prefix."""
    try:
        return _provider_variables(VARIABLE_PREFIX, provider)
    except ModelSettingsError as exc:
        raise AssistantConfigError(str(exc)) from exc


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AssistantConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise AssistantConfigError(f"{path}: cannot read: {type(exc).__name__}") from exc
    _keys(data, _KEYS, "")
    return AssistantConfig(
        owner_time_zone=_zone(data["owner_time_zone"]),
        model=_model(data["model"]),
        action_words=_action_words(data["action_words"]),
        memory=MemoryConfig(**_section(data, "memory")),
        limits=LimitsConfig(**_section(data, "limits")),
        polling=PollingConfig(**_section(data, "polling")),
        database=DatabaseConfig(**_section(data, "database")),
    )


def _keys(data, expected: set[str], prefix: str) -> None:
    if not isinstance(data, dict):
        raise AssistantConfigError(f"{prefix.rstrip('.') or 'config'}: must be a mapping")
    for key in sorted(expected - data.keys()):
        raise AssistantConfigError(f"{prefix}{key}: required setting is missing")
    for key in sorted(set(data) - expected, key=str):
        raise AssistantConfigError(f"{prefix}{key}: unknown setting")


def _section(data, name: str) -> dict[str, int]:
    bounds = _SECTIONS[name]
    section = data[name]
    _keys(section, set(bounds), f"{name}.")
    return {key: _int(section[key], f"{name}.{key}", *bounds[key]) for key in bounds}


def _int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssistantConfigError(f"{name}: must be an integer, got {value!r}")
    if not low <= value <= high:
        raise AssistantConfigError(f"{name}: {value} is outside {low}-{high}")
    return value


def _model(section) -> ModelSettings:
    _keys(section, _MODEL_KEYS, "model.")
    try:
        return parse_model_settings(section, timeout_bounds=MODEL_TIMEOUT_BOUNDS)
    except ModelSettingsError as exc:
        raise AssistantConfigError(str(exc)) from exc


def _zone(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssistantConfigError("owner_time_zone: must be a non-empty string")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise AssistantConfigError(f"owner_time_zone: {value!r} is not a known zone") from exc
    return value


def _action_words(value) -> tuple[str, ...]:
    low, high = ACTION_WORDS_BOUNDS
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise AssistantConfigError(f"action_words: must be a list of {low}-{high} words")
    for word in value:
        if not isinstance(word, str) or not re.fullmatch(r"[a-z]+", word):
            raise AssistantConfigError(f"action_words: {word!r} must be lowercase letters only")
    if len(set(value)) != len(value):
        raise AssistantConfigError("action_words: must not repeat a word")
    return tuple(value)
