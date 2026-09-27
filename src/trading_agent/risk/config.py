"""Load and validate config/risk.yaml (specs/002-risk-gate/contracts/risk-config.md).

Strict by design: every key required, no unknown keys, exact types and ranges.
A misspelled limit is an error, not a silently missing one. Numbers become
Decimals. The version is a hash of the file's bytes, so any edit changes it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml


class RiskConfigError(Exception):
    pass


@dataclass(frozen=True)
class UniverseConfig:
    listing: str
    min_market_cap_usd: Decimal
    min_avg_daily_dollar_volume_usd: Decimal
    min_share_price_usd: Decimal


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: Decimal
    cash_reserve_pct: Decimal
    stop_loss_pct: Decimal
    max_orders_per_day: int
    daily_loss_halt_pct: Decimal
    max_buy_price_tolerance_pct: Decimal
    universe: UniverseConfig
    version: str


_TOP_KEYS = {
    "max_position_pct",
    "cash_reserve_pct",
    "stop_loss_pct",
    "max_orders_per_day",
    "daily_loss_halt_pct",
    "max_buy_price_tolerance_pct",
    "universe",
}
_UNIVERSE_KEYS = {
    "listing",
    "min_market_cap_usd",
    "min_avg_daily_dollar_volume_usd",
    "min_share_price_usd",
}


def _check_keys(data: dict, expected: set[str], prefix: str) -> None:
    for key in sorted(expected - data.keys()):
        raise RiskConfigError(f"{prefix}{key}: required setting is missing")
    for key in sorted(set(data) - expected, key=str):
        raise RiskConfigError(f"{prefix}{key}: unknown setting")


def _number(data: dict, key: str, path: str, low, high, *, low_open: bool, high_open: bool):
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RiskConfigError(f"{path}: must be a number, got {value!r}")
    number = Decimal(str(value))
    too_low = number <= low if low_open else number < low
    too_high = high is not None and (number >= high if high_open else number > high)
    if too_low or too_high:
        raise RiskConfigError(f"{path}: {number} is out of range")
    return number


def _pct(data, key, *, low_open=True, high_open=False, low=0, high=100):
    return _number(
        data, key, key, Decimal(low), Decimal(high), low_open=low_open, high_open=high_open
    )


def load_config(path: Path) -> RiskConfig:
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        raise RiskConfigError(f"risk config not found: {path}") from None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RiskConfigError(f"risk config is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise RiskConfigError("risk config must be a mapping of settings")

    _check_keys(data, _TOP_KEYS, "")

    orders = data["max_orders_per_day"]
    if isinstance(orders, bool) or not isinstance(orders, int):
        raise RiskConfigError(f"max_orders_per_day: must be a whole number, got {orders!r}")
    if orders < 0:
        raise RiskConfigError(f"max_orders_per_day: {orders} is out of range")

    universe = data["universe"]
    if not isinstance(universe, dict):
        raise RiskConfigError(f"universe: must be a mapping, got {universe!r}")
    _check_keys(universe, _UNIVERSE_KEYS, "universe.")
    listing = universe["listing"]
    if listing != "us_common_equity":
        raise RiskConfigError(
            f"universe.listing: only 'us_common_equity' is supported, got {listing!r}"
        )

    def universe_floor(key: str, *, strictly_positive: bool) -> Decimal:
        return _number(
            universe,
            key,
            f"universe.{key}",
            Decimal(0),
            None,
            low_open=strictly_positive,
            high_open=False,
        )

    return RiskConfig(
        max_position_pct=_pct(data, "max_position_pct"),
        cash_reserve_pct=_pct(data, "cash_reserve_pct", low_open=False, high_open=True),
        stop_loss_pct=_pct(data, "stop_loss_pct", high_open=True),
        max_orders_per_day=orders,
        daily_loss_halt_pct=_pct(data, "daily_loss_halt_pct", high_open=True),
        max_buy_price_tolerance_pct=_pct(
            data, "max_buy_price_tolerance_pct", low_open=False, high=10
        ),
        universe=UniverseConfig(
            listing=listing,
            min_market_cap_usd=universe_floor("min_market_cap_usd", strictly_positive=False),
            min_avg_daily_dollar_volume_usd=universe_floor(
                "min_avg_daily_dollar_volume_usd", strictly_positive=False
            ),
            min_share_price_usd=universe_floor("min_share_price_usd", strictly_positive=True),
        ),
        version=hashlib.sha256(raw).hexdigest()[:12],
    )
