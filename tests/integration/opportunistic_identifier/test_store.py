"""The Opportunistic Identifier's database access as ta_opportunistic_identifier
(specs/011-opportunistic-identifier FR-014, FR-015, FR-016; research O8, O14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from tests.integration.helpers import as_role, attempt
from trading_agent.opportunistic_identifier.service import NotAutocommit, PgOIStore, ReportRow

ROLE = "ta_opportunistic_identifier"
NOW = datetime.now(UTC)
LATER = NOW + timedelta(hours=6)
SOURCE = {
    "title": "ACME quote",
    "url": "https://finnhub.io/quote/ACME",
    "publisher": "Finnhub",
    "published_at": "2026-10-08T14:55:00+00:00",
    "relevance": "primary",
}


def row(symbol="AAPL", direction="buy", size=Decimal("4.5"), **changes) -> ReportRow:
    fields = dict(
        symbol=symbol,
        direction=direction,
        conviction=4,
        suggested_size_pct=size,
        sources=[SOURCE, SOURCE, SOURCE],
        rationale_md="Thesis.",
        expires_at=LATER,
    )
    fields.update(changes)
    return ReportRow(**fields)


def no_action(text="Opportunistic Identifier run: nothing_argued.") -> ReportRow:
    return ReportRow(None, "no_action", None, None, [], text, LATER)


def _rows(conn):
    return conn.execute(
        "SELECT agent, symbol, direction, conviction, suggested_size_pct, sources, rationale_md,"
        " expires_at FROM reports ORDER BY symbol NULLS FIRST"
    ).fetchall()


def _insert(conn, agent, symbol, direction, expires_at, sources="[]"):
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources,"
        " rationale_md, expires_at, generated_at) VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'x', %s,"
        " %s)",
        (
            agent,
            symbol,
            direction,
            None if direction == "no_action" else 3,
            None if direction == "no_action" else 4,
            sources,
            expires_at,
            NOW - timedelta(hours=2),
        ),
    )


BUY_SOURCES = (
    '[{"title": "t", "url": "https://x.example/y", "publisher": "p", "published_at": "z"}]'
)


def test_rows_are_written_as_the_opportunistic_identifier(conn):
    with as_role(conn, ROLE):
        PgOIStore(conn, _allow_savepoints=True).write([row(), row("MSFT", size=Decimal("2"))])
    aapl, msft = _rows(conn)
    assert aapl["agent"] == msft["agent"] == "opportunistic_identifier"
    assert (aapl["direction"], aapl["conviction"]) == ("buy", 4)
    assert aapl["suggested_size_pct"] == Decimal("4.500") and msft["suggested_size_pct"] == 2
    assert aapl["sources"] == [SOURCE] * 3 and aapl["expires_at"] == LATER
    assert aapl["rationale_md"] == "Thesis."


def test_a_no_action_row_has_nulls_and_no_sources(conn):
    with as_role(conn, ROLE):
        PgOIStore(conn, _allow_savepoints=True).write([no_action()])
    (only,) = _rows(conn)
    assert (only["symbol"], only["conviction"], only["suggested_size_pct"]) == (None, None, None)
    assert only["sources"] == [] and only["direction"] == "no_action"


def test_a_run_is_written_all_or_nothing(conn):
    bad = row("NVDA", size=Decimal(0))  # a buy at 0 violates the check
    with pytest.raises(psycopg.errors.CheckViolation):
        with as_role(conn, ROLE):
            PgOIStore(conn, _allow_savepoints=True).write([row(), row("MSFT"), bad])
    assert _rows(conn) == []


def test_open_symbols_are_this_agents_unexpired_actionable_rows_only(conn):
    _insert(conn, "opportunistic_identifier", "AAPL", "buy", LATER, BUY_SOURCES)
    _insert(
        conn, "opportunistic_identifier", "MSFT", "buy", NOW - timedelta(minutes=1), BUY_SOURCES
    )
    _insert(conn, "opportunistic_identifier", None, "no_action", LATER)
    _insert(conn, "research", "NVDA", "buy", LATER, BUY_SOURCES)
    _insert(conn, "opportunistic_identifier", "TSLA", "buy", LATER, BUY_SOURCES)
    with as_role(conn, ROLE):
        got = PgOIStore(conn, _allow_savepoints=True).open_symbols(NOW)
    assert set(got) == {"AAPL", "TSLA"}


def test_row_level_security_refuses_another_agents_rows(conn):
    statement = (
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources,"
        " rationale_md, expires_at) VALUES ('research', 'AAPL', 'buy', 3, 4,"
        ' \'[{"title": "t", "url": "u", "publisher": "p", "published_at": "z"}]\','
        " 'x', now() + interval '1 hour')"
    )
    assert attempt(conn, ROLE, statement) == "denied"


@pytest.mark.parametrize(
    "table",
    ["positions", "decisions", "orders", "risk_verdicts", "account_snapshots", "journal"],
)
def test_it_cannot_read_the_portfolio_or_the_decision_chain(conn, table):
    assert attempt(conn, ROLE, f"SELECT 1 FROM {table}") == "denied"


def test_the_store_requires_autocommit_outside_tests(conn):
    with pytest.raises(NotAutocommit):
        PgOIStore(conn)


def test_on_an_autocommit_connection_a_failed_run_leaves_no_rows(database_url):
    """Production's connection is autocommit, so only the store's own transaction keeps a
    failed write from leaving its first rows behind (FR-015)."""
    marker = "oi-autocommit-all-or-nothing-test"
    conn = psycopg.connect(database_url, autocommit=True)
    try:
        conn.execute(f"SET ROLE {ROLE}")
        store = PgOIStore(conn)
        bad = row("NVDA", size=Decimal(0), rationale_md=marker)
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


def test_a_row_expiring_before_it_was_generated_is_refused_under_the_constraint_the_service_maps(
    conn,
):
    # The service turns exactly this violation into window_closed (exit 5): pin the name.
    from trading_agent.opportunistic_identifier.service import EXPIRES_CONSTRAINT

    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        with as_role(conn, ROLE):
            PgOIStore(conn, _allow_savepoints=True).write([row(expires_at=NOW)])
    assert (
        caught.value.diag.constraint_name
        == EXPIRES_CONSTRAINT
        == ("reports_expires_after_generated")
    )
