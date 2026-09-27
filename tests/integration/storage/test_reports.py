"""US1: analyst reports, each writer confined to its own rows."""

from __future__ import annotations

import json

import pytest

from tests.integration.helpers import as_role, attempt, sqlstate_of

CHECK_VIOLATION = "23514"

_INSERT = """
    INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct,
                         sources, rationale_md, generated_at, expires_at)
    VALUES (%(agent)s, %(symbol)s, %(direction)s, %(conviction)s, %(suggested_size_pct)s,
            %(sources)s::jsonb, %(rationale_md)s,
            coalesce(%(generated_at)s::timestamptz, now()),
            coalesce(%(expires_at)s::timestamptz, now() + interval '6 hours'))
    RETURNING id
"""

SOURCE = {
    "title": "Q3 earnings beat",
    "url": "https://example.com/aapl-q3",
    "publisher": "Example Wire",
    "published_at": "2026-09-27T12:00:00Z",
}


def report(**overrides) -> dict:
    row = {
        "agent": "research",
        "symbol": "AAPL",
        "direction": "buy",
        "conviction": 4,
        "suggested_size_pct": 5,
        "sources": json.dumps([SOURCE]),
        "rationale_md": "Earnings beat; guidance raised.",
        "generated_at": None,
        "expires_at": None,
    }
    row.update(overrides)
    return row


def no_action(**overrides) -> dict:
    row = report(
        symbol=None,
        direction="no_action",
        conviction=None,
        suggested_size_pct=None,
        sources="[]",
        rationale_md="Nothing cleared the bar this run.",
    )
    row.update(overrides)
    return row


def test_each_analyst_writes_its_own_rows_readable_by_permitted_readers(conn):
    with as_role(conn, "ta_research"):
        research_id = conn.execute(_INSERT, report()).fetchone()["id"]
    with as_role(conn, "ta_opportunistic_identifier"):
        oi_id = conn.execute(_INSERT, report(agent="opportunistic_identifier")).fetchone()["id"]

    with as_role(conn, "ta_portfolio_manager"):
        seen = {r["id"] for r in conn.execute("SELECT id FROM reports")}
    assert {research_id, oi_id} <= seen


@pytest.mark.parametrize(
    ("role", "foreign_agent"),
    [
        ("ta_research", "opportunistic_identifier"),
        ("ta_opportunistic_identifier", "research"),
    ],
)
def test_analyst_cannot_write_the_other_agents_rows(conn, role, foreign_agent):
    assert attempt(conn, role, _INSERT, report(agent=foreign_agent)) == "denied"


def test_no_action_run_persists_with_no_symbol(conn):
    with as_role(conn, "ta_research"):
        report_id = conn.execute(_INSERT, no_action()).fetchone()["id"]
    row = conn.execute(
        "SELECT symbol, direction FROM reports WHERE id = %s", (report_id,)
    ).fetchone()
    assert row == {"symbol": None, "direction": "no_action"}


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(no_action(symbol="AAPL"), id="no_action-with-symbol"),
        pytest.param(report(symbol=None), id="buy-without-symbol"),
        pytest.param(report(conviction=0), id="conviction-0"),
        pytest.param(report(conviction=6), id="conviction-6"),
        pytest.param(report(conviction=None), id="buy-without-conviction"),
        pytest.param(report(suggested_size_pct=0), id="size-0"),
        pytest.param(report(suggested_size_pct=101), id="size-101"),
        pytest.param(report(sources="[]"), id="buy-without-sources"),
        pytest.param(report(sources=json.dumps(SOURCE)), id="sources-not-array"),
        pytest.param(
            report(generated_at="2026-09-28T14:00:00Z", expires_at="2026-09-28T14:00:00Z"),
            id="expires-not-after-generated",
        ),
        pytest.param(report(agent="other"), id="unknown-agent"),
        pytest.param(report(direction="short"), id="unknown-direction"),
    ],
)
def test_invalid_report_rejected_by_check(conn, row):
    assert sqlstate_of(conn, _INSERT, row) == CHECK_VIOLATION


def test_expired_report_excluded_from_open_query_without_any_update(conn):
    past = report(generated_at="2026-01-02T14:00:00Z", expires_at="2026-01-02T21:00:00Z")
    expired_id = conn.execute(_INSERT, past).fetchone()["id"]
    open_id = conn.execute(_INSERT, report()).fetchone()["id"]

    open_ids = {r["id"] for r in conn.execute("SELECT id FROM reports WHERE expires_at > now()")}
    assert open_id in open_ids
    assert expired_id not in open_ids


def test_reports_has_no_status_column(conn):
    row = conn.execute(
        "SELECT count(*) AS n FROM information_schema.columns "
        "WHERE table_name = 'reports' AND column_name = 'status'"
    ).fetchone()
    assert row["n"] == 0
