"""Order history as the read role (specs/014 tasks T021; US3 independent test)."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from tests.integration.storage import chain
from trading_agent.dashboard.store import ORDER_HISTORY, PostgresStore
from trading_agent.storage.migrate import apply_migrations

DAY = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)


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
        "admin": admin,
        "ids": ids,
        "store": PostgresStore(make_conninfo(url, user=login, password=password), 10),
    }
    admin.close()
    with psycopg.connect(server_url, autocommit=True) as server:
        server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login)))


def _at(conn, verdict_id, when: datetime) -> None:
    conn.execute("UPDATE risk_verdicts SET evaluated_at = %s WHERE id = %s", (when, verdict_id))


def _seed(conn) -> dict:
    ids = {}
    # 1. a decision rejected by the gate: no order
    d1 = chain.insert_decision(conn, [], "AAPL")
    ids["rejected"] = chain.insert_verdict(conn, d1, approved=False)
    # 2. approved and filled
    d2 = chain.insert_decision(conn, [], "MSFT")
    ids["filled"] = chain.insert_verdict(conn, d2, approved=True)
    chain.insert_order(conn, ids["filled"], symbol="MSFT")
    conn.execute(
        "UPDATE orders SET status = 'filled', fill_qty = 10, fill_price = 187.1, "
        "broker_reason = 'ok <b>' WHERE risk_verdict_id = %s",
        (ids["filled"],),
    )
    # 3. approved and refused by Execution
    d3 = chain.insert_decision(conn, [], "AAPL")
    ids["refused"] = chain.insert_verdict(conn, d3, approved=True)
    conn.execute(
        "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
        "VALUES (%s, 'quote_above_ceiling', %s::jsonb, now())",
        (ids["refused"], json.dumps({"ceiling": "190.00"})),
    )
    # 4. a stop-loss exit
    trigger = chain.insert_trigger(conn, "MU", "160")
    ids["stop_loss"] = chain.insert_verdict(conn, None, approved=True, trigger_id=trigger)
    # 5. approved, no outcome yet
    d5 = chain.insert_decision(conn, [], "NVDA")
    ids["in_flight"] = chain.insert_verdict(conn, d5, approved=True)
    for offset, name in enumerate(("rejected", "filled", "refused", "stop_loss", "in_flight")):
        _at(conn, ids[name], DAY + timedelta(hours=offset))
    return ids


def fetch(world, symbol=None, since=None, until=None, limit=51, offset=0) -> list[dict]:
    with world["store"].read() as reader:
        return reader.orders(symbol, since, until, limit, offset)


def by_id(rows) -> dict:
    return {str(r["id"]): r for r in rows}


def test_each_case_appears_exactly_once_newest_first(world):
    rows = fetch(world)
    ids = world["ids"]
    assert [str(r["id"]) for r in rows] == [
        str(ids[n]) for n in ("in_flight", "stop_loss", "refused", "filled", "rejected")
    ]


def test_a_rejected_decision_has_its_decision_the_rule_and_no_outcome(world):
    row = by_id(fetch(world))[str(world["ids"]["rejected"])]
    assert row["verdict"] == "rejected" and row["rejection_rule"] == "max_position_pct"
    assert row["d_symbol"] == "AAPL" and row["direction"] == "buy"
    assert row["size_pct"] == Decimal("5.000") and row["quote_at_decision"] == Decimal("187.25")
    assert row["trigger_id"] is None and row["order_id"] is None and row["refusal_reason"] is None


def test_an_approved_and_filled_order_has_its_status_prices_and_reason(world):
    row = by_id(fetch(world))[str(world["ids"]["filled"])]
    assert row["verdict"] == "approved" and row["approved_order"]["symbol"] == "AAPL"
    assert row["status"] == "filled" and row["fill_qty"] == Decimal("10")
    assert row["fill_price"] == Decimal("187.1") and row["limit_price"] == Decimal("187.25")
    assert row["broker_reason"] == "ok <b>" and row["refusal_reason"] is None


def test_a_refused_verdict_has_the_refusal_not_an_order(world):
    row = by_id(fetch(world))[str(world["ids"]["refused"])]
    assert row["order_id"] is None and row["refusal_reason"] == "quote_above_ceiling"
    assert row["refusal_details"] == {"ceiling": "190.00"}
    assert isinstance(row["refused_at"], datetime)


def test_a_stop_loss_exit_has_the_trigger_in_place_of_a_decision(world):
    row = by_id(fetch(world))[str(world["ids"]["stop_loss"])]
    assert row["decision_id"] is None and row["d_symbol"] is None
    assert row["t_symbol"] == "MU" and row["observed_price"] == Decimal("160")
    assert isinstance(row["observed_at"], datetime)


def test_an_approved_verdict_with_no_outcome_has_neither(world):
    row = by_id(fetch(world))[str(world["ids"]["in_flight"])]
    assert row["verdict"] == "approved" and row["order_id"] is None
    assert row["refusal_reason"] is None


def test_filter_by_symbol_covers_decisions_and_triggers(world):
    ids = world["ids"]
    assert {str(r["id"]) for r in fetch(world, symbol="AAPL")} == {
        str(ids["rejected"]),
        str(ids["refused"]),
    }
    assert {str(r["id"]) for r in fetch(world, symbol="MU")} == {str(ids["stop_loss"])}
    assert fetch(world, symbol="ZZZ") == []


def test_filter_by_dates_includes_the_start_and_excludes_the_end(world):
    ids = world["ids"]
    got = fetch(world, since=DAY + timedelta(hours=1), until=DAY + timedelta(hours=3))
    assert {str(r["id"]) for r in got} == {str(ids["filled"]), str(ids["refused"])}
    only_from = fetch(world, since=DAY + timedelta(hours=3))
    assert {str(r["id"]) for r in only_from} == {str(ids["stop_loss"]), str(ids["in_flight"])}
    only_to = fetch(world, until=DAY + timedelta(hours=1))
    assert {str(r["id"]) for r in only_to} == {str(ids["rejected"])}


def test_filters_combine(world):
    got = fetch(world, symbol="AAPL", since=DAY + timedelta(hours=1))
    assert {str(r["id"]) for r in got} == {str(world["ids"]["refused"])}


def test_limit_and_offset_page_through_in_order(world):
    first = fetch(world, limit=2, offset=0)
    second = fetch(world, limit=2, offset=2)
    third = fetch(world, limit=2, offset=4)
    assert len(first) == len(second) == 2 and len(third) == 1
    assert [r["id"] for r in first + second + third] == [r["id"] for r in fetch(world)]
    assert fetch(world, limit=2, offset=50) == []


def test_the_51st_row_page_flag_with_sixty_verdicts(world):
    admin = world["admin"]
    made = []
    for i in range(55):
        decision = chain.insert_decision(admin, [], "BULK")
        verdict = chain.insert_verdict(admin, decision, approved=False)
        _at(admin, verdict, DAY - timedelta(days=1, minutes=i))
        made.append(verdict)
    try:
        page_one = fetch(world, symbol="BULK", limit=51, offset=0)
        assert len(page_one) == 51  # 50 shown, the 51st says another page exists
        page_two = fetch(world, symbol="BULK", limit=51, offset=50)
        assert len(page_two) == 5
        assert not {r["id"] for r in page_one[:50]} & {r["id"] for r in page_two}
    finally:
        admin.execute("DELETE FROM risk_verdicts WHERE id = ANY(%s)", (made,))
        admin.execute("DELETE FROM decisions WHERE symbol = 'BULK'")


def test_the_sql_binds_every_value_and_never_formats_one(world):
    assert "%(symbol)s" in ORDER_HISTORY and "{" not in ORDER_HISTORY
    hostile = "AAPL'; DROP TABLE risk_verdicts; --"
    assert fetch(world, symbol=hostile) == []
    assert len(fetch(world)) == 5
