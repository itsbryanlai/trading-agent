"""The journal's database role can write `journal` and nothing else (research J14, ADR 0022).

`ta_journal` reads the day's facts and upserts one `journal` row. The rest is guarded
the other way round too: the trading path can't read what the journal writes.
"""

from __future__ import annotations

import pytest

from tests.integration.helpers import as_role, attempt
from tests.integration.storage.factories import probe

UPSERT = """
    INSERT INTO journal (trading_day, equity_open, equity_close, summary_md,
                         per_agent_attribution)
    VALUES ('2026-10-09', 100000, 101000, 'summary', '{}'::jsonb)
    ON CONFLICT (trading_day) DO UPDATE SET
        equity_close = EXCLUDED.equity_close,
        summary_md = EXCLUDED.summary_md,
        per_agent_attribution = EXCLUDED.per_agent_attribution,
        written_at = now()
"""

NOT_WRITABLE = (
    "reports",
    "decisions",
    "decision_reports",
    "risk_verdicts",
    "orders",
    "positions",
    "account_snapshots",
    "execution_refusals",
    "stop_loss_triggers",
    "system_state",
)

LOGIN = "ta_journal_probe_login"


@pytest.fixture(params=["group role", "login"])
def journal_role(request, conn):
    """`ta_journal` itself, and a login made here that is a member of `ta_journal` only, as
    `ta_journal_login` is (storage/logins.py). Both are rolled back with the test."""
    if request.param == "group role":
        return "ta_journal"
    conn.execute(
        f"CREATE ROLE {LOGIN} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
    )
    conn.execute(f"GRANT ta_journal TO {LOGIN}")
    groups = conn.execute(
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
        "JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = %s",
        (LOGIN,),
    ).fetchall()
    assert [g["rolname"] for g in groups] == ["ta_journal"]
    return LOGIN


def test_the_journal_role_can_upsert_a_day_twice(conn, journal_role):
    with as_role(conn, journal_role):
        conn.execute(UPSERT)
        conn.execute(UPSERT)
        count = conn.execute("SELECT count(*) AS n FROM journal").fetchone()["n"]
    assert count == 1


@pytest.mark.parametrize("table", NOT_WRITABLE)
@pytest.mark.parametrize("op", ["I", "U"])
def test_the_journal_role_can_not_write_anything_but_the_journal(conn, journal_role, table, op):
    assert attempt(conn, journal_role, probe(table, op)) == "denied"


def test_the_journal_role_can_not_delete_from_the_journal(conn, journal_role):
    assert attempt(conn, journal_role, probe("journal", "D")) == "denied"


def test_the_journal_role_can_not_read_system_state(conn, journal_role):
    assert attempt(conn, journal_role, probe("system_state", "S")) == "denied"


@pytest.mark.parametrize("role", ["ta_risk_gate", "ta_execution"])
def test_the_trading_path_can_not_read_the_journal(conn, role):
    assert attempt(conn, role, probe("journal", "S")) == "denied"
