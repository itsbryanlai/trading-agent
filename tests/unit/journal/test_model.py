"""The plain values are frozen, and the unpriced reasons are the contract's set."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from trading_agent.journal.model import UNPRICED_REASONS, Holding, Price, RunOutcome


def test_values_are_frozen():
    holding = Holding("AAPL", Decimal("5"), Decimal("229.15"), date(2026, 10, 9))
    with pytest.raises(dataclasses.FrozenInstanceError):
        holding.weight_pct = Decimal("6")  # type: ignore[misc]


def test_the_unpriced_reasons_are_the_contracts():
    assert set(UNPRICED_REASONS) == {
        "not_today",
        "no_price",
        "not_permitted",
        "rate_limited",
        "unavailable",
        "deadline",
        "malformed",
    }


def test_defaults():
    assert RunOutcome("wrote").row is None
    assert Price("AAPL", None, "no_price").close is None
