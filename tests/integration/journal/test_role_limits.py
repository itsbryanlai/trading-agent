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
    "risk_verdicts",
    "orders",
    "positions",
    "account_snapshots",
)


def test_the_journal_role_can_upsert_a_day_twice(conn):
    with as_role(conn, "ta_journal"):
        conn.execute(UPSERT)
        conn.execute(UPSERT)
        count = conn.execute("SELECT count(*) AS n FROM journal").fetchone()["n"]
    assert count == 1


@pytest.mark.parametrize("table", NOT_WRITABLE)
@pytest.mark.parametrize("op", ["I", "U"])
def test_the_journal_role_can_not_write_anything_but_the_journal(conn, table, op):
    assert attempt(conn, "ta_journal", probe(table, op)) == "denied"


def test_the_journal_role_can_not_read_system_state(conn):
    assert attempt(conn, "ta_journal", probe("system_state", "S")) == "denied"


@pytest.mark.parametrize("role", ["ta_risk_gate", "ta_execution"])
def test_the_trading_path_can_not_read_the_journal(conn, role):
    assert attempt(conn, role, probe("journal", "S")) == "denied"
