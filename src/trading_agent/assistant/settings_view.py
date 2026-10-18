"""The `settings` menu entry: allowlisted values from the deployed config files
(research A16; contracts/query-menu.md "`settings` allowlist").

Reads with `yaml.safe_load` and copies only the listed paths. `env`, `module` and every other
key are never copied. It doesn't use the other components' loaders (siblings) and doesn't
validate: a missing file, a malformed file or a missing key shows as "unavailable".
"""

from __future__ import annotations

from pathlib import Path

import yaml

UNAVAILABLE = "unavailable"
SECTIONS = ("risk", "schedule", "models")

_RISK_KEYS = (
    "max_position_pct",
    "cash_reserve_pct",
    "stop_loss_pct",
    "max_orders_per_day",
    "daily_loss_halt_pct",
    "max_buy_price_tolerance_pct",
)
_SCHEDULE_AGENTS = ("research", "opportunistic_identifier", "portfolio_manager")
_SCHEDULE_KEYS = (
    "enabled",
    "timeout_minutes",
    "daily_at",
    "interval_minutes",
    "window_start",
    "window_end",
    "morning_session",
    "min_spacing_minutes",
    "report_wait_minutes",
    "last_start",
    "before_close_minutes",
    "run_while_paused",
)
_MODEL_FILES = {
    "research": "research.yaml",
    "opportunistic_identifier": "opportunistic_identifier.yaml",
    "portfolio_manager": "portfolio_manager.yaml",
    "assistant": "assistant.yaml",
}
_SCALARS = (str, int, float, bool, type(None))


def read(config_dir: Path, section: str | None) -> dict:
    """`{section: values}` for one section, or for all three when `section` is None."""
    wanted = SECTIONS if not section else (section,)
    builders = {"risk": _risk, "schedule": _schedule, "models": _models}
    return {name: builders[name](Path(config_dir)) for name in wanted if name in builders}


def _load(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _scalar(source, key: str):
    value = source.get(key, UNAVAILABLE) if isinstance(source, dict) else UNAVAILABLE
    return value if isinstance(value, _SCALARS) else UNAVAILABLE


def _risk(config_dir: Path):
    data = _load(config_dir / "risk.yaml")
    if data is None:
        return UNAVAILABLE
    result = {key: _scalar(data, key) for key in _RISK_KEYS}
    universe = data.get("universe")
    if isinstance(universe, dict):
        result["universe"] = {
            str(key): value for key, value in universe.items() if isinstance(value, _SCALARS)
        }
    else:
        result["universe"] = UNAVAILABLE
    return result


def _schedule(config_dir: Path):
    data = _load(config_dir / "schedule.yaml")
    if data is None:
        return UNAVAILABLE
    result: dict = {}
    for agent in _SCHEDULE_AGENTS:
        block = data.get(agent)
        if not isinstance(block, dict):
            result[agent] = UNAVAILABLE
            continue
        result[agent] = {
            key: _scalar(block, key) for key in _SCHEDULE_KEYS if key in block
        } or UNAVAILABLE
    return result


def _models(config_dir: Path) -> dict:
    result: dict = {}
    for component, filename in _MODEL_FILES.items():
        data = _load(config_dir / filename)
        model = data.get("model") if data is not None else None
        if not isinstance(model, dict):
            result[component] = UNAVAILABLE
            continue
        result[component] = {
            "provider": _scalar(model, "provider"),
            "name": _scalar(model, "name"),
        }
    return result
