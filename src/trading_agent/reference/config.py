"""Load and validate config/reference_data.yaml (contracts/reference-data-interface.md).

Strict, like risk.yaml: every key required, no unknown keys, exact types and
ranges. A bad file means the job refuses to start (research D11).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from trading_agent.reference.symbols import is_plausible_ticker

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "reference_data.yaml"

_KEYS = {"seed_symbols", "calls_per_minute"}
MIN_CALLS_PER_MINUTE = 1
MAX_CALLS_PER_MINUTE = 300


class ReferenceConfigError(Exception):
    pass


@dataclass(frozen=True)
class ReferenceConfig:
    seed_symbols: tuple[str, ...]
    calls_per_minute: int


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> ReferenceConfig:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ReferenceConfigError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(data, dict):
        raise ReferenceConfigError(f"{path}: must be a mapping")
    for key in sorted(_KEYS - data.keys()):
        raise ReferenceConfigError(f"{key}: required setting is missing")
    for key in sorted(set(data) - _KEYS, key=str):
        raise ReferenceConfigError(f"{key}: unknown setting")

    seeds = data["seed_symbols"]
    if not isinstance(seeds, list) or not all(isinstance(s, str) for s in seeds):
        raise ReferenceConfigError("seed_symbols: must be a list of strings")
    for symbol in seeds:
        if not is_plausible_ticker(symbol):
            raise ReferenceConfigError(f"seed_symbols: {symbol!r} is not a US ticker")
    if len(set(seeds)) != len(seeds):
        raise ReferenceConfigError("seed_symbols: duplicates")

    rate = data["calls_per_minute"]
    if isinstance(rate, bool) or not isinstance(rate, int):
        raise ReferenceConfigError(f"calls_per_minute: must be an integer, got {rate!r}")
    if not MIN_CALLS_PER_MINUTE <= rate <= MAX_CALLS_PER_MINUTE:
        raise ReferenceConfigError(f"calls_per_minute: {rate} is out of range")
    return ReferenceConfig(seed_symbols=tuple(seeds), calls_per_minute=rate)
