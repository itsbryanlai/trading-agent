"""Migration 0014: `latest_report_time` ignores `no_action` reports (specs/011-
opportunistic-identifier FR-023, research O13; ADR 0011). The orchestrator's event-driven PM
trigger reads this view, so a quiet analyst run must not wake the PM."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.helpers import as_role, attempt
from tests.integration.storage.chain import SOURCES

NOW = datetime.now(UTC)
T1, T2, T3 = NOW - timedelta(hours=3), NOW - timedelta(hours=2), NOW - timedelta(hours=1)


def _report(conn, agent, direction, generated_at):
    buy = direction != "no_action"
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources,"
        " rationale_md, generated_at, expires_at)"
        " VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'x', %s, %s + interval '1 day')",
        (
            agent,
            "AAPL" if buy else None,
            direction,
            3 if buy else None,
            5 if buy else None,
            SOURCES if buy else "[]",
            generated_at,
            generated_at,
        ),
    )


def _latest(conn):
    with as_role(conn, "ta_orchestrator"):
        return conn.execute("SELECT generated_at FROM latest_report_time").fetchone()[
            "generated_at"
        ]


def test_a_newer_no_action_leaves_the_latest_report_time_alone(conn):
    _report(conn, "research", "buy", T1)
    _report(conn, "opportunistic_identifier", "no_action", T2)
    assert _latest(conn) == T1


def test_a_no_action_from_either_analyst_is_ignored(conn):
    _report(conn, "research", "buy", T1)
    _report(conn, "research", "no_action", T2)
    _report(conn, "opportunistic_identifier", "no_action", T3)
    assert _latest(conn) == T1


def test_a_later_buy_moves_it(conn):
    _report(conn, "research", "buy", T1)
    _report(conn, "opportunistic_identifier", "no_action", T2)
    _report(conn, "opportunistic_identifier", "buy", T3)
    assert _latest(conn) == T3


def test_a_sell_report_still_counts(conn):
    _report(conn, "research", "buy", T1)
    conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources,"
        " rationale_md, generated_at, expires_at)"
        " VALUES ('research', 'AAPL', 'sell', 3, 0, %s::jsonb, 'x', %s, %s)",
        (SOURCES, T2, T2 + timedelta(days=1)),
    )
    assert _latest(conn) == T2


def test_only_no_action_reports_means_no_latest_time(conn):
    _report(conn, "research", "no_action", T1)
    assert _latest(conn) is None


def test_a_future_dated_report_is_still_ignored(conn):
    _report(conn, "research", "buy", T1)
    _report(conn, "research", "buy", NOW + timedelta(days=3))
    assert _latest(conn) == T1


def test_the_view_keeps_its_one_column_and_its_readers(conn):
    with as_role(conn, "ta_orchestrator"):
        cursor = conn.execute("SELECT * FROM latest_report_time")
        assert [c.name for c in cursor.description] == ["generated_at"]
    for role in ("ta_orchestrator", "ta_assistant", "ta_dashboard"):
        assert attempt(conn, role, "SELECT * FROM latest_report_time") == "allowed"


@pytest.mark.parametrize("role", ["ta_research", "ta_opportunistic_identifier", "ta_risk_gate"])
def test_it_is_still_not_readable_by_roles_that_never_could(conn, role):
    assert attempt(conn, role, "SELECT * FROM latest_report_time") == "denied"


def test_the_view_says_what_it_is_for(conn):
    comment = conn.execute(
        "SELECT obj_description('latest_report_time'::regclass, 'pg_class') AS comment"
    ).fetchone()["comment"]
    assert comment == (
        "Newest report that argues something (excludes no_action), dated up to now: "
        "the orchestrator's event-driven PM trigger (feature 011 FR-023)."
    )
