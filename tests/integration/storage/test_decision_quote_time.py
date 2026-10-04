"""Migration 0012: decisions.quote_time, the trade time of the quote the PM recorded
(specs/008-portfolio-manager data-model.md; ADR 0016 section 4; ADR 0019).

NOT NULL with no default, on purpose: a default would invent a quote time."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.integration.helpers import as_role, sqlstate_of

NOT_NULL_VIOLATION = "23502"
QUOTE_TIME = datetime(2026, 10, 1, 13, 59, 30, tzinfo=UTC)

WITHOUT = (
    "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision) "
    "VALUES ('AAPL', 'buy', 5, 'r', 187.25)"
)
WITH = (
    "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision, "
    "quote_time) VALUES ('AAPL', 'buy', 5, 'r', 187.25, %s) RETURNING id"
)


def test_a_decision_without_a_quote_time_is_rejected(conn):
    assert sqlstate_of(conn, WITHOUT) == NOT_NULL_VIOLATION


def test_a_null_quote_time_is_rejected(conn):
    assert sqlstate_of(conn, WITH, (None,)) == NOT_NULL_VIOLATION


def test_the_column_has_no_default(conn):
    default = conn.execute(
        "SELECT column_default, is_nullable, data_type FROM information_schema.columns "
        "WHERE table_name = 'decisions' AND column_name = 'quote_time'"
    ).fetchone()
    assert default == {
        "column_default": None,
        "is_nullable": "NO",
        "data_type": "timestamp with time zone",
    }


def test_an_aware_quote_time_round_trips(conn):
    decision_id = conn.execute(WITH, (QUOTE_TIME,)).fetchone()["id"]
    row = conn.execute("SELECT quote_time FROM decisions WHERE id = %s", (decision_id,)).fetchone()
    assert row["quote_time"] == QUOTE_TIME


def test_the_portfolio_manager_can_insert_one(conn):
    with as_role(conn, "ta_portfolio_manager"):
        decision_id = conn.execute(WITH, (QUOTE_TIME,)).fetchone()["id"]
    row = conn.execute("SELECT quote_time FROM decisions WHERE id = %s", (decision_id,)).fetchone()
    assert row["quote_time"] == QUOTE_TIME


def test_the_portfolio_manager_still_cannot_leave_the_column_out(conn):
    with as_role(conn, "ta_portfolio_manager"):
        assert sqlstate_of(conn, WITHOUT) == NOT_NULL_VIOLATION


def test_readers_see_the_column_through_their_table_select(conn):
    decision_id = conn.execute(WITH, (QUOTE_TIME,)).fetchone()["id"]
    for role in ("ta_risk_gate", "ta_journal", "ta_assistant", "ta_dashboard"):
        with as_role(conn, role):
            row = conn.execute(
                "SELECT quote_time FROM decisions WHERE id = %s", (decision_id,)
            ).fetchone()
        assert row["quote_time"] == QUOTE_TIME, role
