"""Migration 0009 (specs/004-reference-data research D10): the reference-data job
reads candidate symbols through one view, and can only read and insert its table."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tests.integration.helpers import as_role, attempt
from tests.integration.storage.chain import SOURCES

NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)
DAY = date(2026, 9, 28)


def _report(conn, symbol, generated_at, expires_at, direction="buy"):
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources, "
        "rationale_md, generated_at, expires_at) VALUES ('research', %s, %s, %s, %s, %s::jsonb, "
        "'t', %s, %s)",
        (
            symbol,
            direction,
            None if symbol is None else 3,
            None if symbol is None else 5,
            "[]" if symbol is None else SOURCES,
            generated_at,
            expires_at,
        ),
    )


def _decision(conn, symbol, generated_at):
    conn.execute(
        "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision, "
        "generated_at) VALUES (%s, 'buy', 5, 'r', 100, %s)",
        (symbol, generated_at),
    )


def _rows(conn):
    with as_role(conn, "ta_reference_data"):
        rows = conn.execute(
            "SELECT symbol, source, named_at, active_until FROM reference_candidate_symbols"
        ).fetchall()
    return {(r["symbol"], r["source"]): r for r in rows}


def test_view_columns_are_symbols_and_times_only(conn):
    with as_role(conn, "ta_reference_data"):
        cur = conn.execute("SELECT * FROM reference_candidate_symbols WHERE false")
        assert [c.name for c in cur.description] == [
            "symbol",
            "source",
            "named_at",
            "active_until",
        ]


def test_view_lists_positions_reports_and_decisions(conn):
    conn.execute("INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('HELD', 5, 10)")
    _report(
        conn, "RPT", datetime(2026, 9, 25, 14, tzinfo=UTC), datetime(2026, 9, 25, 20, tzinfo=UTC)
    )
    _report(
        conn, "RPT", datetime(2026, 9, 24, 14, tzinfo=UTC), datetime(2026, 9, 30, 20, tzinfo=UTC)
    )
    _decision(conn, "DEC", datetime(2026, 9, 25, 15, tzinfo=UTC))

    rows = _rows(conn)
    held = rows[("HELD", "position")]
    assert held["named_at"] is None and held["active_until"] is None
    rpt = rows[("RPT", "report")]
    assert rpt["named_at"] == datetime(2026, 9, 25, 14, tzinfo=UTC)
    assert rpt["active_until"] == datetime(2026, 9, 30, 20, tzinfo=UTC)
    dec = rows[("DEC", "decision")]
    assert dec["named_at"] == datetime(2026, 9, 25, 15, tzinfo=UTC)
    assert dec["active_until"] is None


def test_view_has_no_time_filter(conn):
    # An old report still active, and an old expired one: both listed. The FR-001
    # window is applied in code, never against the database clock (D10).
    _report(conn, "LONG", datetime(2026, 8, 1, 14, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    _report(conn, "OLD", datetime(2026, 1, 5, 14, tzinfo=UTC), datetime(2026, 1, 6, tzinfo=UTC))
    _decision(conn, "OLDD", datetime(2026, 1, 5, 14, tzinfo=UTC))
    rows = _rows(conn)
    assert ("LONG", "report") in rows
    assert ("OLD", "report") in rows
    assert ("OLDD", "decision") in rows


def test_no_action_report_is_not_listed(conn):
    _report(
        conn,
        None,
        datetime(2026, 9, 25, 14, tzinfo=UTC),
        datetime(2026, 9, 25, 20, tzinfo=UTC),
        direction="no_action",
    )
    assert all(symbol is not None for symbol, _ in _rows(conn))


@pytest.mark.parametrize("table", ["positions", "reports", "decisions"])
def test_job_cannot_read_the_base_tables(conn, table):
    assert attempt(conn, "ta_reference_data", f"SELECT symbol FROM {table} WHERE false") == "denied"


def test_job_can_insert_but_not_update(conn):
    insert = (
        "INSERT INTO instrument_reference (symbol, trading_day, security_type, exchange_mic, "
        "market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd) "
        "VALUES ('AAA', %s, 'common_stock', 'XNAS', 1000, 100, 10) "
        "ON CONFLICT (symbol, trading_day) DO NOTHING"
    )
    with as_role(conn, "ta_reference_data"):
        conn.execute(insert, (DAY,))
    assert (
        attempt(conn, "ta_reference_data", "UPDATE instrument_reference SET share_price_usd = 1")
        == "denied"
    )


def test_conflicting_insert_leaves_the_row_unchanged(conn):
    with as_role(conn, "ta_reference_data"):
        conn.execute(
            "INSERT INTO instrument_reference (symbol, trading_day, security_type, exchange_mic, "
            "market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd) "
            "VALUES ('AAA', %s, 'common_stock', 'XNAS', 1000, 100, 10)",
            (DAY,),
        )
        conn.execute(
            "INSERT INTO instrument_reference (symbol, trading_day, security_type, exchange_mic, "
            "market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd) "
            "VALUES ('AAA', %s, 'etf', 'ARCX', 5, 5, 5) "
            "ON CONFLICT (symbol, trading_day) DO NOTHING",
            (DAY,),
        )
    row = conn.execute(
        "SELECT security_type, share_price_usd FROM instrument_reference WHERE symbol = 'AAA'"
    ).fetchone()
    assert row["security_type"] == "common_stock" and row["share_price_usd"] == 10


def test_reports_policies_are_unchanged_by_0009(conn):
    # The policies migration 0002 created, exactly: 0009 must not have touched them.
    rows = conn.execute(
        "SELECT policyname, cmd, roles::text[] AS roles FROM pg_policies "
        "WHERE tablename = 'reports' ORDER BY policyname"
    ).fetchall()
    got = {(r["policyname"], r["cmd"], tuple(sorted(r["roles"]))) for r in rows}
    assert got == {
        ("reports_insert_opportunistic_identifier", "INSERT", ("ta_opportunistic_identifier",)),
        ("reports_insert_research", "INSERT", ("ta_research",)),
        (
            "reports_select",
            "SELECT",
            tuple(
                sorted(
                    [
                        "ta_research",
                        "ta_opportunistic_identifier",
                        "ta_portfolio_manager",
                        "ta_journal",
                        "ta_assistant",
                        "ta_dashboard",
                    ]
                )
            ),
        ),
    }
