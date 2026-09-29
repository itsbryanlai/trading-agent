"""The fake provider behaves as the contract says tests may rely on."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.fakes.market_data import FakeMarketData
from trading_agent.reference.provider import KeyRejected, ProviderUnavailable


def test_scripted_values_and_call_log():
    ticks = iter([1.0, 2.0, 3.0, 4.0])
    fake = FakeMarketData(clock=lambda: next(ticks))
    fake.add("AAPL", market_cap_millions="10", previous_close=None)
    assert fake.list_us_symbols()["AAPL"].mic == "XNGS"
    assert fake.get_profile("AAPL").market_cap_millions == Decimal("10")
    assert fake.get_quote("AAPL").previous_close is None
    assert fake.get_metrics("AAPL").avg_volume_10d_millions == Decimal("25")
    assert [t for t, _, _ in fake.calls] == [1.0, 2.0, 3.0, 4.0]
    assert fake.calls_for("AAPL") == ["get_profile", "get_quote", "get_metrics"]


def test_unlisted_symbol_is_absent_from_the_list():
    fake = FakeMarketData()
    fake.add("GONE", listed=False)
    assert "GONE" not in fake.list_us_symbols()


def test_failure_for_one_symbol_after_successes_then_recovery():
    fake = FakeMarketData()
    fake.add("A")
    fake.add("B")
    fake.fail("get_quote", "A", error=ProviderUnavailable("down"), after=1)
    fake.get_quote("A")
    with pytest.raises(ProviderUnavailable):
        fake.get_quote("A")
    fake.get_quote("B")
    fake.recover("get_quote", "A")
    fake.get_quote("A")


def test_failure_for_any_symbol():
    fake = FakeMarketData()
    fake.fail("list_us_symbols", error=KeyRejected("no"))
    with pytest.raises(KeyRejected):
        fake.list_us_symbols()
