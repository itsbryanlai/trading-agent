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


def candidate(symbol="ACME", **changes):
    """The Candidate an eligible name gives, as the screen builds it."""
    from trading_agent.opportunistic_identifier import screen

    result = screen.assess(*inputs(symbol, **changes), NOW, UNIVERSE, MAX_AGE)
    assert isinstance(result, screen.Candidate), result
    return result


def name_data(symbol="ACME", **changes):
    return candidate(symbol, **changes).data


class Clock:
    """A test clock: `now` stays put, and monotonic time advances when the code sleeps."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.mono = 0.0
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.mono += seconds

    def monotonic(self) -> float:
        return self.mono


def config(universe=(), **changes):
    """The shipped config with a scan list of the test's choosing."""
    from dataclasses import replace

    from trading_agent.opportunistic_identifier.config import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_RISK_PATH,
        load_config,
    )

    return replace(
        load_config(DEFAULT_CONFIG_PATH, DEFAULT_RISK_PATH),
        scan_universe=tuple(sorted(universe)),
        **changes,
    )


def messages(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name == "trading_agent.opportunistic_identifier"
    ]


class FakeConn:
    """Just enough of a psycopg autocommit connection for PgOIStore: `execute(...)` answers
    the open-symbols read, and `transaction()`/`cursor()` collect what is written."""

    autocommit = True

    def __init__(self, open_rows=(), fail_read=False, fail_write=False):
        self.open_rows = list(open_rows)
        self.fail_read, self.fail_write = fail_read, fail_write
        self.written: list = []
        self.closed = False

    def execute(self, statement, params=None):
        import psycopg

        if self.fail_read:
            raise psycopg.OperationalError("lost")
        rows = self.open_rows
        return type("Result", (), {"fetchall": lambda self: rows})()

    def transaction(self):
        import contextlib

        return contextlib.nullcontext()

    def cursor(self):
        import contextlib

        import psycopg

        conn = self

        class Cursor:
            def executemany(self, statement, rows):
                if conn.fail_write:
                    raise psycopg.OperationalError("lost")
                conn.written.extend(rows)

        return contextlib.nullcontext(Cursor())

    def close(self):
        self.closed = True
