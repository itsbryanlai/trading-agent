"""Load and validate config/dashboard.yaml (contracts/dashboard-interface.md).

Strict, like the other components' files: every key required, no unknown keys, integers
(never booleans) within bounds. A bad file means the service refuses to start. Error messages
name the key and never a credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "dashboard.yaml"

_TOP_INTS = {
    "session_hours": (1, 24),
    "page_size": (10, 200),
    "max_sessions": (1, 100),
}
_SECTIONS = {
    "sign_in_delay": {
        "first_seconds": (1, 10),
        "cap_seconds": (1, 300),
        "tracked_addresses": (10, 100_000),
    },
    "database": {"statement_timeout_seconds": (1, 60)},
}
_KEYS = {"owner_time_zone", *_TOP_INTS, *_SECTIONS}


class DashboardConfigError(Exception):
    pass


@dataclass(frozen=True)
class SignInDelayConfig:
    first_seconds: int
    cap_seconds: int
    tracked_addresses: int


@dataclass(frozen=True)
class DatabaseConfig:
    statement_timeout_seconds: int


@dataclass(frozen=True)
class DashboardConfig:
    owner_time_zone: str
    session_hours: int
    page_size: int
    max_sessions: int
    sign_in_delay: SignInDelayConfig
    database: DatabaseConfig


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> DashboardConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise DashboardConfigError(f"{path}: cannot read: {type(exc).__name__}") from exc
    _keys(data, _KEYS, "")
    delay = SignInDelayConfig(**_section(data, "sign_in_delay"))
    if delay.cap_seconds < delay.first_seconds:
        raise DashboardConfigError(
            f"sign_in_delay.cap_seconds: {delay.cap_seconds} is below first_seconds "
            f"{delay.first_seconds}"
        )
    return DashboardConfig(
        owner_time_zone=_zone(data["owner_time_zone"]),
        sign_in_delay=delay,
        database=DatabaseConfig(**_section(data, "database")),
        **{key: _int(data[key], key, *bounds) for key, bounds in _TOP_INTS.items()},
    )


def _keys(data, expected: set[str], prefix: str) -> None:
    if not isinstance(data, dict):
        raise DashboardConfigError(f"{prefix.rstrip('.') or 'config'}: must be a mapping")
    for key in sorted(expected - data.keys()):
        raise DashboardConfigError(f"{prefix}{key}: required setting is missing")
    for key in sorted(set(data) - expected, key=str):
        raise DashboardConfigError(f"{prefix}{key}: unknown setting")


def _section(data, name: str) -> dict[str, int]:
    bounds = _SECTIONS[name]
    section = data[name]
    _keys(section, set(bounds), f"{name}.")
    return {key: _int(section[key], f"{name}.{key}", *bounds[key]) for key in bounds}


def _int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DashboardConfigError(f"{name}: must be an integer, got {value!r}")
    if not low <= value <= high:
        raise DashboardConfigError(f"{name}: {value} is outside {low}-{high}")
    return value


def _zone(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DashboardConfigError("owner_time_zone: must be a non-empty string")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise DashboardConfigError(f"owner_time_zone: {value!r} is not a known zone") from exc
    return value
