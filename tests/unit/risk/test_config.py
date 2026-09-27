from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from trading_agent.risk.config import RiskConfigError, load_config

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "risk.yaml"


def _base() -> dict:
    return yaml.safe_load(REPO_CONFIG.read_text())


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "risk.yaml"
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    return path


def test_repo_config_loads_with_contract_values():
    config = load_config(REPO_CONFIG)
    assert config.max_position_pct == Decimal("8")
    assert config.cash_reserve_pct == Decimal("20")
    assert config.stop_loss_pct == Decimal("20")
    assert config.max_orders_per_day == 5
    assert config.daily_loss_halt_pct == Decimal("20")
    assert config.max_buy_price_tolerance_pct == Decimal("1")
    assert config.universe.listing == "us_common_equity"
    assert config.universe.min_market_cap_usd == Decimal("500000000")
    assert config.universe.min_avg_daily_dollar_volume_usd == Decimal("10000000")
    assert config.universe.min_share_price_usd == Decimal("5")


def test_no_loaded_number_is_a_float():
    config = load_config(REPO_CONFIG)
    values = [*vars(config).values(), *vars(config.universe).values()]
    assert not any(isinstance(v, float) for v in values)


def test_version_is_12_hex_chars_and_changes_with_a_comment(tmp_path):
    text = REPO_CONFIG.read_text()
    a = load_config(_write(tmp_path, text)).version
    (tmp_path / "b").mkdir()
    b = load_config(_write(tmp_path / "b", text + "\n# a comment\n")).version
    assert len(a) == 12 and all(c in "0123456789abcdef" for c in a)
    assert a != b


def test_missing_file(tmp_path):
    with pytest.raises(RiskConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml(tmp_path):
    with pytest.raises(RiskConfigError, match="YAML"):
        load_config(_write(tmp_path, "max_position_pct: [8\n"))


TOP_KEYS = [
    "max_position_pct",
    "cash_reserve_pct",
    "stop_loss_pct",
    "max_orders_per_day",
    "daily_loss_halt_pct",
    "max_buy_price_tolerance_pct",
    "universe",
]
UNIVERSE_KEYS = [
    "listing",
    "min_market_cap_usd",
    "min_avg_daily_dollar_volume_usd",
    "min_share_price_usd",
]


@pytest.mark.parametrize("key", TOP_KEYS)
def test_missing_top_level_key(tmp_path, key):
    data = _base()
    del data[key]
    with pytest.raises(RiskConfigError, match=key):
        load_config(_write(tmp_path, data))


@pytest.mark.parametrize("key", UNIVERSE_KEYS)
def test_missing_universe_key(tmp_path, key):
    data = _base()
    del data["universe"][key]
    with pytest.raises(RiskConfigError, match=f"universe.{key}"):
        load_config(_write(tmp_path, data))


@pytest.mark.parametrize(
    ("path", "value"),
    [(("max_postion_pct",), 8), (("universe", "min_float_usd"), 1)],
    ids=["top-level-typo", "universe-unknown"],
)
def test_unknown_key(tmp_path, path, value):
    data = _base()
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(RiskConfigError, match=path[-1]):
        load_config(_write(tmp_path, data))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("max_orders_per_day",), "5"),
        (("max_orders_per_day",), True),
        (("max_orders_per_day",), 5.5),
        (("stop_loss_pct",), True),
        (("stop_loss_pct",), "20"),
        (("universe",), "everything"),
        (("universe", "min_share_price_usd"), None),
    ],
)
def test_wrong_type(tmp_path, path, value):
    data = _base()
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(RiskConfigError, match=".".join(path)):
        load_config(_write(tmp_path, data))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("max_position_pct",), 0),
        (("max_position_pct",), 101),
        (("cash_reserve_pct",), 100),
        (("cash_reserve_pct",), -1),
        (("stop_loss_pct",), 100),
        (("stop_loss_pct",), 0),
        (("daily_loss_halt_pct",), 0),
        (("max_orders_per_day",), -1),
        (("max_buy_price_tolerance_pct",), 11),
        (("max_buy_price_tolerance_pct",), -1),
        (("universe", "min_share_price_usd"), 0),
        (("universe", "min_market_cap_usd"), -1),
        (("universe", "listing"), "all"),
    ],
)
def test_out_of_range(tmp_path, path, value):
    data = _base()
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(RiskConfigError, match=".".join(path)):
        load_config(_write(tmp_path, data))


def test_top_level_not_a_mapping(tmp_path):
    with pytest.raises(RiskConfigError):
        load_config(_write(tmp_path, "- 1\n- 2\n"))
