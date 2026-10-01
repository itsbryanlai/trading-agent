"""Research's writes and reads as ta_research (specs/007-research-agent FR-001, FR-012,
FR-014; research R8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from tests.integration.helpers import as_role, attempt
from trading_agent.research.service import PgResearchStore, ReportRow

NOW = datetime.now(UTC)
LATER = NOW + timedelta(hours=6)


def _next_session_morning() -> datetime:
    """09:00 ET-ish (14:00 UTC) on the next session after today, so its reports'
    expiry is always after the database's now(): a fixed date would expire."""
    from trading_agent.risk import calendar

    day = calendar.trading_day(NOW) + timedelta(days=1)
    while not calendar.is_session(day):
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC)


NOW_SESSION = _next_session_morning()
SOURCE = {
    "title": "Apple beats",
    "url": "https://news.example.com/apple",
    "publisher": "Example Wire",
    "published_at": "2026-10-01T11:00:00+00:00",
}


def row(symbol="AAPL", direction="buy", size=Decimal("4.5"), **changes) -> ReportRow:
    fields = dict(
        symbol=symbol,
        direction=direction,
        conviction=4,
        suggested_size_pct=size,
        sources=[SOURCE],
        rationale_md="Thesis.",
        expires_at=LATER,
    )
    fields.update(changes)
    return ReportRow(**fields)


def no_action(text="Research run failed: model_unavailable.") -> ReportRow:
    return ReportRow(None, "no_action", None, None, [], text, LATER)


def _rows(conn):
    return conn.execute(
        "SELECT agent, symbol, direction, conviction, suggested_size_pct, sources, rationale_md,"
        " expires_at FROM reports WHERE agent = 'research' ORDER BY symbol NULLS FIRST"
    ).fetchall()


def test_rows_are_written_as_research(conn):
    with as_role(conn, "ta_research"):
        store = PgResearchStore(conn, _allow_savepoints=True)
        store.write([row(), row("MSFT", "sell", Decimal(0))])
    aapl, msft = _rows(conn)
    assert aapl["agent"] == "research" and aapl["conviction"] == 4
    assert aapl["suggested_size_pct"] == Decimal("4.500")
    assert aapl["sources"] == [SOURCE] and aapl["expires_at"] == LATER
    assert (msft["direction"], msft["suggested_size_pct"]) == ("sell", Decimal("0.000"))


def test_a_failure_no_action_row_has_nulls_and_no_sources(conn):
    with as_role(conn, "ta_research"):
        PgResearchStore(conn, _allow_savepoints=True).write([no_action()])
    (only,) = _rows(conn)
    assert (only["symbol"], only["conviction"], only["suggested_size_pct"]) == (None, None, None)
    assert only["sources"] == [] and only["direction"] == "no_action"


def test_a_run_is_written_all_or_nothing(conn):
    bad = row("NVDA", "buy", Decimal(0))  # a buy at 0 violates the check
    with pytest.raises(psycopg.errors.CheckViolation):
        with as_role(conn, "ta_research"):
            PgResearchStore(conn, _allow_savepoints=True).write([row(), row("MSFT"), bad])
    assert _rows(conn) == []


def test_open_reports_are_unexpired_actionable_research_rows(conn):
    insert = (
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources,"
        " rationale_md, expires_at, generated_at) VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'x', %s,"
        " %s)"
    )
    sources = (
        '[{"title": "t", "url": "https://x.example/y", "publisher": "p", "published_at": "z"}]'
    )
    earlier = NOW - timedelta(hours=2)
    conn.execute(insert, ("research", "AAPL", "buy", 3, 4, sources, LATER, earlier))
    conn.execute(
        insert, ("research", "MSFT", "sell", 3, 0, sources, NOW - timedelta(minutes=1), earlier)
    )
    conn.execute(insert, ("opportunistic_identifier", "NVDA", "buy", 3, 4, sources, LATER, earlier))
    conn.execute(insert, ("research", None, "no_action", None, None, "[]", LATER, earlier))
    with as_role(conn, "ta_research"):
        got = PgResearchStore(conn, _allow_savepoints=True).open_reports(NOW)
    assert got == [("AAPL", "buy")]


def test_row_level_security_refuses_the_other_analysts_rows(conn):
    statement = (
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources,"
        " rationale_md, expires_at) VALUES ('opportunistic_identifier', 'AAPL', 'buy', 3, 4,"
        ' \'[{"title": "t", "url": "u", "publisher": "p", "published_at": "z"}]\','
        " 'x', now() + interval '1 hour')"
    )
    assert attempt(conn, "ta_research", statement) == "denied"


@pytest.mark.parametrize(
    "table", ["positions", "decisions", "orders", "account_snapshots", "decision_reports"]
)
def test_research_cannot_read_the_portfolio_or_the_decision_chain(conn, table):
    assert attempt(conn, "ta_research", f"SELECT 1 FROM {table}") == "denied"


def test_the_store_requires_autocommit_outside_tests(conn):
    from trading_agent.research.service import NotAutocommit

    with pytest.raises(NotAutocommit):
        PgResearchStore(conn)


def test_on_an_autocommit_connection_a_failed_run_leaves_no_rows(database_url):
    """Production's connection is autocommit, so only the store's own transaction
    keeps a failed write from leaving its first rows behind (FR-012)."""
    marker = "autocommit-all-or-nothing-test"
    conn = psycopg.connect(database_url, autocommit=True)
    try:
        conn.execute("SET ROLE ta_research")
        store = PgResearchStore(conn)
        bad = row("NVDA", "buy", Decimal(0), rationale_md=marker)
        with pytest.raises(psycopg.errors.CheckViolation):
            store.write([row(rationale_md=marker), row("MSFT", rationale_md=marker), bad])
        count = conn.execute(
            "SELECT count(*) FROM reports WHERE rationale_md = %s", (marker,)
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.execute("RESET ROLE")
        conn.execute("DELETE FROM reports WHERE rationale_md = %s", (marker,))
        conn.close()


def test_a_run_whose_model_fails_writes_one_failure_row(conn):
    from tests.fakes.model import FakeModel
    from tests.fakes.news import FakeNews
    from tests.unit.research.support import Clock, article, config
    from trading_agent.research.ports import ModelUnavailable
    from trading_agent.research.service import ResearchRun

    clock = Clock(NOW_SESSION)
    with as_role(conn, "ta_research"):
        ResearchRun(
            FakeNews(general=[article("apple", related=("AAPL",), at=NOW_SESSION)]),
            FakeModel(error=ModelUnavailable()),
            PgResearchStore(conn, _allow_savepoints=True),
            config(),
            clock=clock,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        ).run()
    (only,) = _rows(conn)
    assert only["direction"] == "no_action" and only["symbol"] is None
    assert (only["conviction"], only["suggested_size_pct"], only["sources"]) == (None, None, [])
    assert only["rationale_md"].startswith("Research run failed: model_unavailable.")
