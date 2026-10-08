"""Load and validate config/journal.yaml (contracts/journal-interface.md).

Strict, like the other components' files: every key required, no unknown keys, integers
(never booleans) within bounds. A bad file means the run refuses to start. Error messages
name the key and never a credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "journal.yaml"

_INTS = {
    "holding_sessions": (1, 60),
    "finnhub_calls_per_minute": (1, 300),
    "fetch_deadline_seconds": (30, 540),
    "close_grace_minutes": (0, 30),
}


class JournalConfigError(Exception):
    pass


@dataclass(frozen=True)
class JournalConfig:
    holding_sessions: int
    finnhub_calls_per_minute: int
    fetch_deadline_seconds: int
    close_grace_minutes: int


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> JournalConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise JournalConfigError(f"{path}: cannot read: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise JournalConfigError("config: must be a mapping")
    for key in sorted(_INTS.keys() - data.keys()):
        raise JournalConfigError(f"{key}: required setting is missing")
    for key in sorted(set(data) - _INTS.keys(), key=str):
        raise JournalConfigError(f"{key}: unknown setting")
    return JournalConfig(**{key: _int(data[key], key, *_INTS[key]) for key in _INTS})


def _int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JournalConfigError(f"{name}: must be an integer, got {value!r}")
    if not low <= value <= high:
        raise JournalConfigError(f"{name}: {value} is outside {low}-{high}")
    return value
