"""Migration 0007 (feature 003): order identifiers per verdict (ADR 0012), the
order status and limit-price rules, execution_refusals, and one outcome per
approval."""

from __future__ import annotations

import pytest

from tests.integration.helpers import as_role, attempt, sqlstate_of
from tests.integration.storage.chain import (
    insert_decision,
    insert_order,
    insert_refusal,
    insert_report,
    insert_verdict,
    order_id_for,
)

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
INTEGRITY_VIOLATION = "23000"


def _verdict(conn, approved=True):
    return insert_verdict(conn, insert_decision(conn, [insert_report(conn)]), approved=approved)


def _insert(conn, order_id, verdict, status="submitted", limit_price=None):
    return sqlstate_of(
        conn,
        "INSERT INTO orders (id, risk_verdict_id, status, limit_price) VALUES (%s, %s, %s, %s)",
        (order_id, verdict, status, limit_price),
    )


@pytest.mark.parametrize(
    "order_id",
    [
        "2026-09-28-AAPL-buy",  # the pre-ADR 0012 format
        "2026-09-28-aapl-buy-{v}",
        "2026-09-28-AAPL-hold-{v}",
        "26-09-28-AAPL-buy-{v}",
        "2026-09-28-AAPL-buy-{v}x",
    ],
)
def test_malformed_identifier_rejected(conn, order_id):
    verdict = _verdict(conn)
    oid = order_id.format(v=str(verdict)[:8])
    assert _insert(conn, oid, verdict, limit_price="200") == CHECK_VIOLATION


def test_identifier_must_name_its_own_verdict(conn):
    mine, other = _verdict(conn), _verdict(conn)
    assert _insert(conn, order_id_for(other), mine, limit_price="200") == CHECK_VIOLATION
    assert _insert(conn, order_id_for(mine), mine, limit_price="200") is None


def test_share_class_symbols_allowed(conn):
    verdict = _verdict(conn)
    assert _insert(conn, order_id_for(verdict, symbol="BRK.B"), verdict, limit_price="1") is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [("expired", None), ("partially_filled", None), ("pending", CHECK_VIOLATION)],
)
def test_order_statuses(conn, status, expected):
    verdict = _verdict(conn)
    assert _insert(conn, order_id_for(verdict), verdict, status, "200") == expected


def test_a_buy_has_a_limit_price_and_a_sell_does_not(conn):
    buy, sell = _verdict(conn), _verdict(conn)
    assert _insert(conn, order_id_for(buy), buy) == CHECK_VIOLATION
    assert _insert(conn, order_id_for(sell, "sell"), sell, limit_price="200") == CHECK_VIOLATION
    assert _insert(conn, order_id_for(buy), buy, limit_price="0") == CHECK_VIOLATION
    assert _insert(conn, order_id_for(sell, "sell"), sell) is None


def test_refusal_names_a_contract_reason_and_an_approved_verdict(conn):
    verdict = _verdict(conn)
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
            "VALUES (%s, 'felt_like_it', '{}'::jsonb, now())",
            (verdict,),
        )
        == CHECK_VIOLATION
    )
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
            "VALUES (%s, 'trading_paused', '[]'::jsonb, now())",
            (verdict,),
        )
        == CHECK_VIOLATION
    )
    rejected = _verdict(conn, approved=False)
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
            "VALUES (%s, 'trading_paused', '{}'::jsonb, now())",
            (rejected,),
        )
        == FOREIGN_KEY_VIOLATION
    )


def test_one_refusal_per_verdict(conn):
    verdict = _verdict(conn)
    insert_refusal(conn, verdict)
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
            "VALUES (%s, 'approval_expired', '{}'::jsonb, now())",
            (verdict,),
        )
        == UNIQUE_VIOLATION
    )


def test_an_order_then_a_refusal_for_the_same_verdict_is_impossible(conn):
    verdict = _verdict(conn)
    insert_order(conn, verdict)
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
            "VALUES (%s, 'approval_expired', '{}'::jsonb, now())",
            (verdict,),
        )
        == INTEGRITY_VIOLATION
    )


def test_a_refusal_then_an_order_for_the_same_verdict_is_impossible(conn):
    verdict = _verdict(conn)
    insert_refusal(conn, verdict)
    assert _insert(conn, order_id_for(verdict), verdict, limit_price="200") == INTEGRITY_VIOLATION


def test_execution_reads_only_the_pause_flag(conn):
    assert attempt(conn, "ta_execution", "SELECT trading_paused FROM system_state") == "allowed"
    for column in ("halt_triggered_on", "daily_starting_equity", "baseline_trading_day"):
        statement = f"SELECT {column} FROM system_state"
        assert attempt(conn, "ta_execution", statement) == "denied"


def test_execution_cannot_repoint_an_order_but_can_record_its_fills(conn):
    verdict = _verdict(conn)
    insert_order(conn, verdict)
    repoint = "UPDATE orders SET risk_verdict_id = gen_random_uuid()"
    assert attempt(conn, "ta_execution", repoint) == "denied"
    with as_role(conn, "ta_execution"):
        conn.execute("UPDATE orders SET status = 'filled', fill_qty = 10, fill_price = 187")


def test_invalid_symbol_is_a_contract_reason(conn):
    # Migration 0008 (research E16).
    verdict = _verdict(conn)
    insert_refusal(conn, verdict, "invalid_symbol")
