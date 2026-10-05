"""docs/operations/observe-queries.sql runs as ta_dashboard, the role behind the
owner's read-only login (feature 010, T022-T023). Every statement must run without
a privilege error, and the two check blocks must answer correctly on a database we
control."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.integration.helpers import as_role

QUERIES = Path(__file__).resolve().parents[3] / "docs" / "operations" / "observe-queries.sql"
# The login's own group role, so the test sees exactly what the owner's login sees.
READ_ROLE = "ta_dashboard"


def statements() -> list[str]:
    """The file's statements: comment lines dropped, split on the semicolon."""
    body = "\n".join(
        line for line in QUERIES.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    return [part.strip() for part in body.split(";") if part.strip()]


# Positions in the file: the last two statements are the check blocks.
PRE_OPEN = 8
FIRST_TRADING_DAY = 9


def _checks(conn, index: int) -> dict[str, bool]:
    with as_role(conn, READ_ROLE):
        rows = conn.execute(statements()[index]).fetchall()
    return {row["check_name"]: row["ok"] for row in rows}


def test_the_file_has_the_expected_statements():
    found = statements()
    assert len(found) == 10
    assert all(s.upper().startswith("SELECT") for s in found)  # read-only, by construction
    assert not re.search(
        r"\b(insert|update|delete|alter|drop|grant|create)\b", "\n".join(found), re.I
    )


@pytest.mark.parametrize("index", range(10))
def test_every_statement_runs_as_the_read_only_role(conn, index):
    statement = statements()[index]
    with as_role(conn, READ_ROLE):
        conn.execute(statement).fetchall()


def test_pre_open_fails_until_the_pause_is_set_then_passes(conn):
    assert _checks(conn, PRE_OPEN) == {"trading_paused is true": False, "positions is empty": True}
    with as_role(conn, "ta_dashboard_control"):
        conn.execute("UPDATE system_state SET trading_paused = true, updated_at = now()")
    assert all(_checks(conn, PRE_OPEN).values())


def test_pre_open_fails_with_a_held_position(conn):
    conn.execute("UPDATE system_state SET trading_paused = true")
    conn.execute("INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('AAPL', 1, 10)")
    assert _checks(conn, PRE_OPEN) == {
        "trading_paused is true": True,
        "positions is empty": False,
    }


def test_first_trading_day_check_on_an_empty_database_flags_the_missing_evidence(conn):
    conn.execute("UPDATE system_state SET trading_paused = true")
    checks = _checks(conn, FIRST_TRADING_DAY)
    assert len(checks) == 8
    assert checks["an account snapshot exists from today"] is False
    assert checks["at least one decision exists"] is False
    assert checks["trading_paused is true"] is True
    assert checks["orders is empty"] is True


def test_first_trading_day_check_passes_for_paused_rejections_and_fails_on_an_approval(conn):
    conn.execute("UPDATE system_state SET trading_paused = true")
    conn.execute(
        "INSERT INTO account_snapshots (equity, cash, buying_power) VALUES (1000, 1000, 1000)"
    )
    decision = conn.execute(
        "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision,"
        " quote_time) VALUES ('AAPL', 'buy', 5, 'r', 10, now()) RETURNING id"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO risk_verdicts (decision_id, verdict, rejection_rule, trading_day,"
        " config_version) VALUES (%s, 'rejected', 'trading_paused', current_date, 'v')",
        (decision,),
    )
    assert all(_checks(conn, FIRST_TRADING_DAY).values())

    conn.execute("UPDATE risk_verdicts SET rejection_rule = 'max_position_pct'")
    failing = [name for name, ok in _checks(conn, FIRST_TRADING_DAY).items() if not ok]
    assert failing == ["every buy rejection is trading_paused, market_closed or decision_stale"]

    conn.execute(
        "UPDATE risk_verdicts SET verdict = 'approved', rejection_rule = NULL,"
        ' approved_order = \'{"symbol": "AAPL", "side": "buy"}\''
    )
    failing = [name for name, ok in _checks(conn, FIRST_TRADING_DAY).items() if not ok]
    assert failing == ["no buy verdict is approved"]
