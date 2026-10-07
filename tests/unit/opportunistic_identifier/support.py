"""Builders shared by the Opportunistic Identifier's unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trading_agent.opportunistic_identifier.ports import (
    CompanyProfile,
    Fundamentals,
    Listing,
    Quote,
)
from trading_agent.risk.config import load_config as load_risk_config

ROOT = Path(__file__).resolve().parents[3]
# The test clock: Thursday 2026-10-08, 11:00 ET = 15:00 UTC (EDT).
NOW = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
FRESH = NOW - timedelta(minutes=5)
MAX_AGE = timedelta(minutes=15)
UNIVERSE = load_risk_config(ROOT / "config" / "risk.yaml").universe
FAKE_KEY = "fake-not-real"


def D(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def inputs(
    symbol="ACME",
    *,
    type="Common Stock",
    mic="XNGS",
    description="ACME CORP",
    conflicting=False,
    market_cap_millions="3000000",
    currency="USD",
    industry="Software",
    previous_close="200",
    current="190",
    quote_time=FRESH,
    **fundamentals,
):
    """(symbol, listing, profile, quote, fundamentals): a name that passes every check,
    unless an argument changes it. `current` 190 on 200 is down 5%."""
    values = {
        "avg_volume_10d_millions": "25",
        "high_52w": "250",
        "low_52w": "150",
        "pe_ttm": "20",
        "pb": "3",
        **fundamentals,
    }
    return (
        symbol,
        Listing(symbol, type, mic, description, conflicting),
        CompanyProfile(symbol, D(market_cap_millions), currency, industry),
        Quote(symbol, D(current), D(previous_close), quote_time),
        Fundamentals(symbol, **{k: D(v) for k, v in values.items()}),
    )
