"""`--check`: owner-run, read-only, key only, no database (research D12)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes.market_data import FakeMarketData
from trading_agent.reference import __main__ as runner
from trading_agent.reference.provider import KeyRejected, NotPermitted

FAKE_KEY = "test-key-not-real"
NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)


def check(args, fake):
    lines = []

    def connect(*a, **k):
        raise AssertionError("--check must not touch the database")

    code = runner.main(
        ["--check", *args],
        provider_factory=lambda key: fake,
        connect=connect,
        sleep=lambda s: None,
        clock=lambda: NOW,
        out=lines.append,
    )
    return code, lines


@pytest.fixture(autouse=True)
def key_only(monkeypatch):
    monkeypatch.setenv(runner.KEY_VARIABLE, FAKE_KEY)
    monkeypatch.delenv(runner.DATABASE_VARIABLE, raising=False)


def test_prints_one_line_per_symbol_and_writes_nothing():
    fake = FakeMarketData()
    fake.add("AAPL", market_cap_millions="1415993", previous_close="150.25")
    fake.add("BRK.B", mic="XNYS", market_cap_millions="100", avg_volume_10d_millions="10")
    code, lines = check(["AAPL", "BRK.B", "bad$"], fake)
    assert code == runner.EXIT_OK
    assert lines[0].startswith("AAPL common_stock XNAS market_cap_usd=1,415,993,000,000.00")
    # What the mappings and the stale-quote rule received, for the owner to check.
    assert "provider type='Common Stock' mic='XNGS' currency='USD' c=201 pc=150.25" in lines[0]
    assert "t=2026-09-28T11:00:00+00:00" in lines[0]
    assert lines[1].startswith("BRK.B failed: implausible_dollar_volume")
    assert lines[2] == "bad$ failed: invalid_symbol"
    assert FAKE_KEY not in "\n".join(lines)


def test_unlisted_symbol():
    fake = FakeMarketData()
    fake.add("GONE", listed=False)
    code, lines = check(["GONE"], fake)
    assert code == runner.EXIT_OK and len(lines) == 1
    assert lines[0].startswith("GONE failed: not_listed (currency='USD'")


def test_not_permitted_symbol():
    fake = FakeMarketData()
    fake.add("AAPL")
    fake.fail("get_profile", "AAPL", error=NotPermitted("HTTP 403"))
    code, lines = check(["AAPL"], fake)
    assert code == runner.EXIT_OK and lines == ["AAPL failed: not_permitted"]


def test_key_rejected_returns_2():
    fake = FakeMarketData()
    fake.fail("list_us_symbols", error=KeyRejected("HTTP 401"))
    code, lines = check(["AAPL"], fake)
    assert code == runner.EXIT_REFUSED and "key check failed" in lines[0]


def test_needs_the_key(monkeypatch):
    monkeypatch.delenv(runner.KEY_VARIABLE)
    code, _ = check(["AAPL"], FakeMarketData())
    assert code == runner.EXIT_REFUSED
