"""Agent activity queries as the read role (specs/014 tasks T022)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from tests.integration.storage import chain
from trading_agent.dashboard.store import PostgresStore
from trading_agent.storage.migrate import apply_migrations

DAY = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
FAR = DAY + timedelta(days=30)


@pytest.fixture(scope="module")
def world(make_database, server_url):
    url = make_database()
    apply_migrations(url)
    login, password = f"ta_test_dashboard_{secrets.token_hex(4)}", secrets.token_hex(8)
    admin = psycopg.connect(url, row_factory=dict_row, autocommit=True)
    admin.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE ta_dashboard").format(
            sql.Identifier(login), sql.Literal(password)
        )
    )
    ids = _seed(admin)
    yield {
        "ids": ids,
        "store": PostgresStore(make_conninfo(url, user=login, password=password), 10),
    }
    admin.close()
    with psycopg.connect(server_url, autocommit=True) as server:
        server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login)))


def _report_at(conn, report_id, when):
    conn.execute(
        "UPDATE reports SET generated_at = %s, expires_at = %s WHERE id = %s",
        (when, when + timedelta(days=1), report_id),
    )


def _run(conn, agent, key, outcome, when, detail=None, *, started=True, day=None):
    conn.execute(
        "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, slot_at, "
        "started_at, finished_at, outcome, detail) VALUES "
        "(%s, %s, 'scheduled', %s, %s, %s, %s, %s, %s)",
        (
            agent,
            (day or when).date(),
            key,
            when,
            when if started else None,
            when + timedelta(minutes=5) if started and outcome != "running" else None,
            outcome,
            detail,
        ),
    )


def _seed(conn) -> dict:
    ids = {}
    ids["r_research"] = chain.insert_report(conn, "research", "AAPL")
    ids["r_oi"] = chain.insert_report(conn, "opportunistic_identifier", "MU")
    ids["r_none"] = conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, sources,"
        " rationale_md, generated_at, expires_at) VALUES ('opportunistic_identifier', NULL,"
        " 'no_action', NULL, NULL, '[]', 'quiet', now(), now() + interval '1 day') RETURNING id"
    ).fetchone()["id"]
    _report_at(conn, ids["r_research"], DAY)
    _report_at(conn, ids["r_oi"], DAY + timedelta(days=1))
    _report_at(conn, ids["r_none"], DAY + timedelta(days=2))
    ids["d1"] = chain.insert_decision(conn, [ids["r_research"], ids["r_oi"]], "AAPL")
    ids["d2"] = chain.insert_decision(conn, [], "MU")
    conn.execute(
        "UPDATE decisions SET generated_at = %s WHERE id = %s",
        (DAY + timedelta(hours=3), ids["d1"]),
    )
    conn.execute(
        "UPDATE decisions SET generated_at = %s WHERE id = %s",
        (DAY + timedelta(days=1, hours=3), ids["d2"]),
    )
    _run(conn, "research", "research_daily", "succeeded", DAY)
    _run(conn, "research", "research@10:00", "timed_out", DAY + timedelta(days=1), "exit 124")
    _run(conn, "portfolio_manager", "morning_session", "skipped", DAY + timedelta(days=2),
         "trading paused", started=False)  # fmt: skip
    _run(conn, "opportunistic_identifier", "oi@11:00", "failed", DAY + timedelta(days=3), "exit 1")
    _run(conn, "opportunistic_identifier", "oi@12:00", "interrupted", DAY + timedelta(days=4))
    return ids


def reports(world, agent=None, symbol=None, since=None, until=None, limit=100):
    with world["store"].read() as reader:
        return reader.reports(agent, symbol, since, until, limit)


def decisions(world, symbol=None, since=None, until=None, limit=100):
    with world["store"].read() as reader:
        return reader.decisions(symbol, since, until, limit)


def runs(world, agent=None, since=None, until=None, problems=False, limit=100):
    with world["store"].read() as reader:
        return reader.runs(agent, since, until, problems, limit)


def test_reports_from_both_analysts_including_no_action_newest_first(world):
    got = reports(world)
    ids = world["ids"]
    assert [str(r["id"]) for r in got] == [str(ids[k]) for k in ("r_none", "r_oi", "r_research")]
    none = got[0]
    assert none["direction"] == "no_action" and none["symbol"] is None and none["sources"] == []
    first = got[2]
    assert first["conviction"] == 4 and first["sources"][0]["title"] == "Q3 earnings beat"
    assert first["rationale_md"] == "thesis"


def test_reports_filter_by_agent_symbol_and_dates(world):
    ids = world["ids"]
    assert {str(r["id"]) for r in reports(world, agent="research")} == {str(ids["r_research"])}
    assert {str(r["id"]) for r in reports(world, agent="opportunistic_identifier")} == {
        str(ids["r_oi"]),
        str(ids["r_none"]),
    }
    assert {str(r["id"]) for r in reports(world, symbol="MU")} == {str(ids["r_oi"])}
    got = reports(world, since=DAY + timedelta(days=1), until=DAY + timedelta(days=2))
    assert {str(r["id"]) for r in got} == {str(ids["r_oi"])}  # end exclusive
    assert reports(world, agent="research", symbol="MU") == []


def test_a_decision_carries_its_cited_report_ids(world):
    got = {str(d["id"]): d for d in decisions(world)}
    ids = world["ids"]
    cited = got[str(ids["d1"])]["report_ids"]
    assert sorted(str(i) for i in cited) == sorted([str(ids["r_research"]), str(ids["r_oi"])])
    assert got[str(ids["d2"])]["report_ids"] == []
    assert got[str(ids["d1"])]["reasoning_md"] and got[str(ids["d1"])]["quote_at_decision"]


def test_decisions_are_newest_first_and_filter_by_symbol_and_dates(world):
    ids = world["ids"]
    assert [str(d["id"]) for d in decisions(world)] == [str(ids["d2"]), str(ids["d1"])]
    assert [str(d["id"]) for d in decisions(world, symbol="AAPL")] == [str(ids["d1"])]
    got = decisions(world, since=DAY, until=DAY + timedelta(days=1))
    assert [str(d["id"]) for d in got] == [str(ids["d1"])]


def test_runs_cover_all_agents_newest_first_using_start_or_slot_time(world):
    got = runs(world)
    assert [r["slot_key"] for r in got] == [
        "oi@12:00",
        "oi@11:00",
        "morning_session",
        "research@10:00",
        "research_daily",
    ]
    skipped = [r for r in got if r["outcome"] == "skipped"][0]
    assert skipped["started_at"] is None and skipped["detail"] == "trading paused"


def test_runs_filter_by_agent_and_dates(world):
    assert {r["slot_key"] for r in runs(world, agent="research")} == {
        "research_daily",
        "research@10:00",
    }
    assert [r["slot_key"] for r in runs(world, agent="portfolio_manager")] == ["morning_session"]
    got = runs(world, since=DAY + timedelta(days=2), until=DAY + timedelta(days=4))
    assert {r["slot_key"] for r in got} == {"morning_session", "oi@11:00"}  # end exclusive


def test_the_problem_filter_keeps_failed_timed_out_skipped_and_interrupted(world):
    got = runs(world, problems=True)
    assert {r["outcome"] for r in got} == {"failed", "timed_out", "skipped", "interrupted"}
    assert "research_daily" not in {r["slot_key"] for r in got}
    assert [r["outcome"] for r in runs(world, agent="research", problems=True)] == ["timed_out"]


def test_a_succeeded_run_is_not_a_problem_and_running_is_not_either(world):
    assert all(r["outcome"] != "succeeded" for r in runs(world, problems=True))


def test_limit_returns_the_newest_rows(world):
    assert [r["slot_key"] for r in runs(world, limit=2)] == ["oi@12:00", "oi@11:00"]
    assert len(reports(world, limit=1)) == 1 and len(decisions(world, limit=1)) == 1


def test_report_times_maps_ids_to_their_time(world):
    ids = world["ids"]
    with world["store"].read() as reader:
        got = reader.report_times(
            [ids["r_research"], str(ids["r_oi"]), "00000000-0000-0000-0000-000000000000"]
        )
        assert reader.report_times([]) == {}
    assert got == {str(ids["r_research"]): DAY, str(ids["r_oi"]): DAY + timedelta(days=1)}


def test_values_are_bound_never_formatted(world):
    hostile = "AAPL'; DROP TABLE reports; --"
    assert reports(world, symbol=hostile) == [] and decisions(world, symbol=hostile) == []
    assert reports(world, agent="x' OR '1'='1") == []
    assert len(reports(world)) == 3
