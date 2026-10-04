"""A run whose write fails part-way leaves no decision and no link
(specs/008-portfolio-manager spec US4-4, SC-004; research P10).

The whole run is real except the outside world: the PM connects as `ta_portfolio_manager` to
a database it can read and write, and its model and quotes are fakes. One decision is
tampered with after the checker (it cites a report that doesn't exist), so the database
refuses the second decision's link after the first decision and its link went in."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.fakes.market_data import FakeMarketData
from tests.fakes.model import FakeModel
from tests.unit.portfolio_manager.service_support import Clock, Settings, answer_of, decide
from trading_agent.portfolio_manager import __main__ as runner
from trading_agent.portfolio_manager import service
from trading_agent.portfolio_manager.store import PostgresStore, StoreError
from trading_agent.risk import calendar
from trading_agent.storage.migrate import apply_migrations

ROLE = "ta_portfolio_manager"
MISSING = "00000000-0000-0000-0000-000000000000"
SOURCES = json.dumps(
    [{"title": "T", "publisher": "P", "published_at": "x", "relevance": "primary"}]
)


def next_session() -> date:
    day = datetime.now(UTC).date() + timedelta(days=1)
    while not calendar.is_session(day):
        day += timedelta(days=1)
    return day


DAY = next_session()
RUN_START = calendar.open_time(DAY) + timedelta(minutes=30)  # 10:00 ET
CLOSE = calendar.close_time(DAY)


class TamperedStore(PostgresStore):
    """The real store, handed a last decision citing a report that doesn't exist."""

    def write(self, decisions):
        decisions = list(decisions)
        decisions[-1] = replace(decisions[-1], report_ids=(*decisions[-1].report_ids, MISSING))
        super().write(decisions)


@pytest.fixture
def seeded(make_database):
    """A migrated database with two buy reports and today's snapshot, committed."""
    url = make_database()
    apply_migrations(url)
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as admin:
        for symbol in ("AAPL", "MSFT"):
            admin.execute(
                "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct,"
                " sources, rationale_md, generated_at, expires_at)"
                " VALUES ('research', %s, 'buy', 4, 5, %s::jsonb, 'thesis', %s, %s)",
                (symbol, SOURCES, RUN_START - timedelta(hours=1), CLOSE),
            )
        admin.execute(
            "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power)"
            " VALUES (%s, 100000, 100000, 100000)",
            (RUN_START - timedelta(minutes=30),),
        )
    return url


def counts(url):
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as admin:
        return tuple(
            admin.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            for table in ("decisions", "decision_reports")
        )


def quotes():
    fake = FakeMarketData()
    for symbol, price in (("AAPL", "200"), ("MSFT", "300")):
        fake.add(symbol, current=price, quote_time=RUN_START - timedelta(minutes=1))
    return fake


def both_buys():
    return FakeModel(answer_of(decide("AAPL", ids=["R1"]), decide("MSFT", ids=["R2"])))


def pm_connection(url):
    conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
    conn.execute(f"SET ROLE {ROLE}")
    return conn


def test_a_run_whose_write_fails_part_way_leaves_no_decision_and_no_link(seeded):
    clock = Clock(RUN_START)
    conn = pm_connection(seeded)
    try:
        with pytest.raises(StoreError):
            service.run(
                clock=clock,
                config=Settings(),
                store=TamperedStore(conn),
                quotes=quotes(),
                model=both_buys(),
                sleep=clock.sleep,
            )
    finally:
        conn.close()
    assert counts(seeded) == (0, 0)


def test_the_entry_point_exits_three_on_it_and_a_clean_run_then_writes_both(seeded, monkeypatch):
    for name, value in (
        ("PORTFOLIO_MANAGER_DATABASE_URL", "postgresql://fake-not-real@localhost/none"),
        ("PORTFOLIO_MANAGER_FINNHUB_API_KEY", "fake-not-real"),
        ("PORTFOLIO_MANAGER_DASHSCOPE_API_KEY", "fake-not-real"),
        ("PORTFOLIO_MANAGER_QWEN_BASE_URL", "https://qwen.example.test/v1"),
    ):
        monkeypatch.setenv(name, value)  # the clients and the connection are injected below

    def run(store_factory):
        clock = Clock(RUN_START)
        return runner.main(
            [],
            quotes_factory=lambda key: quotes(),
            model_factory=lambda provider, key, settings, *, base_url=None: both_buys(),
            connect=lambda url, **kw: pm_connection(seeded),
            store_factory=store_factory,
            clock=clock,
            sleep=clock.sleep,
        )

    assert run(TamperedStore) == 3
    assert counts(seeded) == (0, 0)
    assert run(PostgresStore) == 0
    assert counts(seeded) == (2, 2)
