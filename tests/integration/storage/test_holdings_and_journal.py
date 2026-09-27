"""US3: holdings, account state, and a journal the trading path can't read."""

from __future__ import annotations

import json

import pytest

from tests.integration.helpers import as_role, attempt, sqlstate_of

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"

_UPSERT_JOURNAL = """
    INSERT INTO journal (trading_day, equity_open, equity_close, summary_md,
                         per_agent_attribution)
    VALUES (%(day)s, 100000, %(close)s, %(summary)s, %(attribution)s::jsonb)
    ON CONFLICT (trading_day) DO UPDATE SET
        equity_close = EXCLUDED.equity_close,
        summary_md = EXCLUDED.summary_md,
        per_agent_attribution = EXCLUDED.per_agent_attribution,
        written_at = now()
"""

ATTRIBUTION = json.dumps(
    {"research": {"return_pct": 0.4}, "opportunistic_identifier": {"return_pct": -0.1}}
)


def test_execution_opens_updates_and_closes_a_position(conn):
    with as_role(conn, "ta_execution"):
        conn.execute(
            "INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('AAPL', 10, 187.25)"
        )
        conn.execute("UPDATE positions SET qty = 15, updated_at = now() WHERE symbol = 'AAPL'")
        held = conn.execute("SELECT qty FROM positions WHERE symbol = 'AAPL'").fetchone()
        assert held["qty"] == 15
        conn.execute("DELETE FROM positions WHERE symbol = 'AAPL'")
    assert conn.execute("SELECT count(*) AS n FROM positions").fetchone()["n"] == 0


@pytest.mark.parametrize(
    ("qty", "price"), [pytest.param(0, 187, id="qty-0"), pytest.param(10, 0, id="price-0")]
)
def test_invalid_position_rejected(conn, qty, price):
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO positions (symbol, qty, avg_entry_price) VALUES ('AAPL', %s, %s)",
            (qty, price),
        )
        == CHECK_VIOLATION
    )


def test_execution_records_an_account_snapshot(conn):
    with as_role(conn, "ta_execution"):
        conn.execute(
            "INSERT INTO account_snapshots (equity, cash, buying_power) "
            "VALUES (100000, 20000, 40000)"
        )
    latest = conn.execute(
        "SELECT equity FROM account_snapshots ORDER BY taken_at DESC LIMIT 1"
    ).fetchone()
    assert latest["equity"] == 100000


@pytest.mark.parametrize(
    ("equity", "buying_power"),
    [pytest.param(-1, 0, id="equity-negative"), pytest.param(0, -1, id="buying-power-negative")],
)
def test_invalid_account_snapshot_rejected(conn, equity, buying_power):
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO account_snapshots (equity, cash, buying_power) VALUES (%s, 0, %s)",
            (equity, buying_power),
        )
        == CHECK_VIOLATION
    )


def test_journal_rerun_replaces_the_days_entry(conn):
    day = {"day": "2026-09-28", "attribution": ATTRIBUTION}
    with as_role(conn, "ta_journal"):
        conn.execute(_UPSERT_JOURNAL, {**day, "close": 100400, "summary": "first run"})
        conn.execute(_UPSERT_JOURNAL, {**day, "close": 100450, "summary": "re-run"})
    rows = conn.execute(
        "SELECT equity_close, summary_md, per_agent_attribution FROM journal "
        "WHERE trading_day = '2026-09-28'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["summary_md"] == "re-run"
    assert set(rows[0]["per_agent_attribution"]) == {"research", "opportunistic_identifier"}


def test_plain_duplicate_journal_day_rejected(conn):
    insert = (
        "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md, "
        "per_agent_attribution) VALUES ('2026-09-28', 1, 1, 's', '{}'::jsonb)"
    )
    conn.execute(insert)
    assert sqlstate_of(conn, insert) == UNIQUE_VIOLATION


def test_attribution_must_be_an_object(conn):
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md, "
            "per_agent_attribution) VALUES ('2026-09-28', 1, 1, 's', '[]'::jsonb)",
        )
        == CHECK_VIOLATION
    )


@pytest.mark.parametrize("role", ["ta_risk_gate", "ta_execution"])
def test_trading_path_cannot_read_the_journal(conn, role):
    assert attempt(conn, role, "SELECT * FROM journal") == "denied"
