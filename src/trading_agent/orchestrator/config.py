"""Load and validate config/schedule.yaml (contracts/orchestrator-interface.md).

Strict, like risk.yaml: every key required, no unknown keys, exact types and
ranges. A bad file means the orchestrator refuses to start. The loader enforces
ADR 0011's bounds, so a looser schedule can't be configured (FR-015), and the
prefix rule, so no agent can be handed another component's credential (FR-008,
ADR 0015).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "schedule.yaml"

RESEARCH = "research"
IDENTIFIER = "opportunistic_identifier"
PORTFOLIO_MANAGER = "portfolio_manager"
AGENTS = (RESEARCH, IDENTIFIER, PORTFOLIO_MANAGER)

# Every variable an agent may receive starts with its own prefix (ADR 0015).
PREFIXES = {
    RESEARCH: "RESEARCH_",
    IDENTIFIER: "OPPORTUNISTIC_IDENTIFIER_",
    PORTFOLIO_MANAGER: "PORTFOLIO_MANAGER_",
}

_MODULE = re.compile(r"trading_agent\.[a-z_]+")
_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")

MARKET_OPEN = time(9, 30)
# ADR 0011's floors and ceilings, which configuration can't loosen (FR-015).
MIN_SPACING_MINUTES = 30
LATEST_PM_START = time(15, 30)
MIN_BEFORE_CLOSE_MINUTES = 30
MIN_REPORT_WAIT_MINUTES = 5
MAX_REPORT_WAIT_MINUTES = 60
MIN_TIMEOUT_MINUTES = 1
MAX_TIMEOUT_MINUTES = 120

_COMMON = {"enabled", "module", "env", "timeout_minutes"}
_KEYS = {
    RESEARCH: _COMMON | {"daily_at", "interval_minutes"},
    IDENTIFIER: _COMMON | {"window_start", "window_end", "interval_minutes"},
    PORTFOLIO_MANAGER: _COMMON
    | {
        "morning_session",
        "min_spacing_minutes",
        "report_wait_minutes",
        "last_start",
        "before_close_minutes",
    },
}


class ScheduleConfigError(Exception):
    pass


@dataclass(frozen=True, kw_only=True)
class AgentConfig:
    name: str
    enabled: bool
    module: str
    env: tuple[str, ...]
    timeout: timedelta


@dataclass(frozen=True, kw_only=True)
class ResearchConfig(AgentConfig):
    daily_at: time
    interval: timedelta | None


@dataclass(frozen=True, kw_only=True)
class IdentifierConfig(AgentConfig):
    window_start: time
    window_end: time
    interval: timedelta


@dataclass(frozen=True, kw_only=True)
class PortfolioManagerConfig(AgentConfig):
    morning_session: time
    min_spacing: timedelta
    report_wait: timedelta
    last_start: time
    before_close: timedelta


@dataclass(frozen=True)
class ScheduleConfig:
    research: ResearchConfig
    identifier: IdentifierConfig
    portfolio_manager: PortfolioManagerConfig

    def agents(self) -> tuple[AgentConfig, ...]:
        return (self.research, self.identifier, self.portfolio_manager)

    def agent(self, name: str) -> AgentConfig:
        return {a.name: a for a in self.agents()}[name]


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> ScheduleConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ScheduleConfigError(f"{path}: cannot read: {exc}") from exc
    _check_keys(data, set(AGENTS), "")
    return ScheduleConfig(
        research=_research(data[RESEARCH]),
        identifier=_identifier(data[IDENTIFIER]),
        portfolio_manager=_portfolio_manager(data[PORTFOLIO_MANAGER]),
    )


def _check_keys(data, expected: set[str], prefix: str) -> None:
    if not isinstance(data, dict):
        raise ScheduleConfigError(f"{prefix or 'file'}: must be a mapping")
    for key in sorted(expected - data.keys()):
        raise ScheduleConfigError(f"{prefix}{key}: required setting is missing")
    for key in sorted(set(data) - expected, key=str):
        raise ScheduleConfigError(f"{prefix}{key}: unknown setting")


def _common(name: str, data) -> dict:
    _check_keys(data, _KEYS[name], f"{name}.")
    enabled = data["enabled"]
    if not isinstance(enabled, bool):
        raise ScheduleConfigError(f"{name}.enabled: must be true or false")
    module = data["module"]
    if not isinstance(module, str) or not _MODULE.fullmatch(module):
        raise ScheduleConfigError(f"{name}.module: must be trading_agent.<name>")
    return {
        "name": name,
        "enabled": enabled,
        "module": module,
        "env": _env(name, data["env"]),
        "timeout": timedelta(
            minutes=_int(data, "timeout_minutes", name, MIN_TIMEOUT_MINUTES, MAX_TIMEOUT_MINUTES)
        ),
    }


def _env(name: str, names) -> tuple[str, ...]:
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ScheduleConfigError(f"{name}.env: must be a list of variable names")
    prefix = PREFIXES[name]
    for variable in names:
        # The prefix rule makes a broker key, another component's login or another
        # agent's key impossible to list, not merely discouraged (ADR 0015).
        if not _ENV_NAME.fullmatch(variable) or not variable.startswith(prefix):
            raise ScheduleConfigError(f"{name}.env: {variable!r} must start with {prefix}")
    if len(set(names)) != len(names):
        raise ScheduleConfigError(f"{name}.env: duplicate names")
    return tuple(names)


def _int(data, key: str, name: str, low: int, high: int | None) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScheduleConfigError(f"{name}.{key}: must be a whole number, got {value!r}")
    if value < low or (high is not None and value > high):
        raise ScheduleConfigError(f"{name}.{key}: {value} is out of range")
    return value


def _time(data, key: str, name: str) -> time:
    value = data[key]
    match = _TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ScheduleConfigError(f"{name}.{key}: must be HH:MM, got {value!r}")
    return time(int(match.group(1)), int(match.group(2)))


def _research(data) -> ResearchConfig:
    common = _common(RESEARCH, data)
    daily_at = _time(data, "daily_at", RESEARCH)
    if daily_at >= MARKET_OPEN:
        raise ScheduleConfigError("research.daily_at: must be before the 09:30 open")
    interval = data["interval_minutes"]
    if interval is not None:
        interval = timedelta(minutes=_int(data, "interval_minutes", RESEARCH, 1, None))
    return ResearchConfig(**common, daily_at=daily_at, interval=interval)


def _identifier(data) -> IdentifierConfig:
    common = _common(IDENTIFIER, data)
    start = _time(data, "window_start", IDENTIFIER)
    end = _time(data, "window_end", IDENTIFIER)
    if end <= start:
        raise ScheduleConfigError("opportunistic_identifier.window_end: must be after window_start")
    interval = timedelta(minutes=_int(data, "interval_minutes", IDENTIFIER, 1, None))
    return IdentifierConfig(**common, window_start=start, window_end=end, interval=interval)


def _portfolio_manager(data) -> PortfolioManagerConfig:
    name = PORTFOLIO_MANAGER
    common = _common(name, data)
    last_start = _time(data, "last_start", name)
    if last_start > LATEST_PM_START:
        raise ScheduleConfigError(f"{name}.last_start: no later than 15:30 (ADR 0011)")
    morning = _time(data, "morning_session", name)
    if morning < MARKET_OPEN or morning >= last_start:
        raise ScheduleConfigError(
            f"{name}.morning_session: must be between the open and last_start"
        )
    spacing = _int(data, "min_spacing_minutes", name, MIN_SPACING_MINUTES, None)
    wait = _int(data, "report_wait_minutes", name, MIN_REPORT_WAIT_MINUTES, MAX_REPORT_WAIT_MINUTES)
    before_close = _int(data, "before_close_minutes", name, MIN_BEFORE_CLOSE_MINUTES, None)
    return PortfolioManagerConfig(
        **common,
        morning_session=morning,
        min_spacing=timedelta(minutes=spacing),
        report_wait=timedelta(minutes=wait),
        last_start=last_start,
        before_close=timedelta(minutes=before_close),
    )
