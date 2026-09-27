"""US2: a decision traceable end-to-end to the order it produced."""

from __future__ import annotations

import pytest

from tests.integration.helpers import as_role, sqlstate_of
from tests.integration.storage.chain import (
    APPROVED_ORDER,
    insert_decision,
    insert_order,
    insert_report,
    insert_verdict,
)

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"


def test_decision_citing_two_agents_reports_records_both(conn):
    research = insert_report(conn, agent="research")
    oi = insert_report(conn, agent="opportunistic_identifier")
    with as_role(conn, "ta_portfolio_manager"):
        decision = insert_decision(conn, [research, oi])

    cited = conn.execute(
        "SELECT r.agent FROM decision_reports dr JOIN reports r ON r.id = dr.report_id "
        "WHERE dr.decision_id = %s",
        (decision,),
    ).fetchall()
    assert sorted(row["agent"] for row in cited) == ["opportunistic_identifier", "research"]


def test_order_traces_back_to_its_reports_through_one_chain(conn):
    research = insert_report(conn, agent="research")
    oi = insert_report(conn, agent="opportunistic_identifier")
    with as_role(conn, "ta_portfolio_manager"):
        decision = insert_decision(conn, [research, oi])
    with as_role(conn, "ta_risk_gate"):
        verdict = insert_verdict(conn, decision)
    with as_role(conn, "ta_execution"):
        order = insert_order(conn, verdict)

    traced = conn.execute(
        """
        SELECT r.id FROM orders o
        JOIN risk_verdicts v ON v.id = o.risk_verdict_id
        JOIN decisions d ON d.id = v.decision_id
        JOIN decision_reports dr ON dr.decision_id = d.id
        JOIN reports r ON r.id = dr.report_id
        WHERE o.id = %s
        """,
        (order,),
    ).fetchall()
    assert {row["id"] for row in traced} == {research, oi}


def test_second_verdict_for_same_decision_rejected(conn):
    decision = insert_decision(conn, [insert_report(conn)])
    insert_verdict(conn, decision)
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO risk_verdicts (decision_id, verdict, rejection_rule) "
            "VALUES (%s, 'rejected', 'cash_reserve_pct')",
            (decision,),
        )
        == UNIQUE_VIOLATION
    )


def test_order_for_rejected_verdict_impossible(conn):
    decision = insert_decision(conn, [insert_report(conn)])
    rejected = insert_verdict(conn, decision, approved=False)
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO orders (id, risk_verdict_id, status) VALUES ('x', %s, 'submitted')",
            (rejected,),
        )
        == FOREIGN_KEY_VIOLATION
    )


def test_order_for_nonexistent_verdict_impossible(conn):
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO orders (id, risk_verdict_id, status) "
            "VALUES ('x', gen_random_uuid(), 'submitted')",
        )
        == FOREIGN_KEY_VIOLATION
    )


def test_second_order_for_same_verdict_rejected(conn):
    verdict = insert_verdict(conn, insert_decision(conn, [insert_report(conn)]))
    insert_order(conn, verdict, order_id="2026-09-28-AAPL-buy")
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO orders (id, risk_verdict_id, status) "
            "VALUES ('2026-09-28-AAPL-sell', %s, 'submitted')",
            (verdict,),
        )
        == UNIQUE_VIOLATION
    )


def test_restart_resubmitting_same_day_symbol_side_recognized_as_duplicate(conn):
    first = insert_verdict(conn, insert_decision(conn, [insert_report(conn)]))
    second = insert_verdict(conn, insert_decision(conn, [insert_report(conn)]))
    insert_order(conn, first, order_id="2026-09-28-AAPL-buy")
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO orders (id, risk_verdict_id, status) "
            "VALUES ('2026-09-28-AAPL-buy', %s, 'submitted')",
            (second,),
        )
        == UNIQUE_VIOLATION
    )


def test_decision_reports_cannot_cite_a_missing_report(conn):
    decision = insert_decision(conn, [])
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO decision_reports (decision_id, report_id) VALUES (%s, gen_random_uuid())",
            (decision,),
        )
        == FOREIGN_KEY_VIOLATION
    )


@pytest.mark.parametrize(
    ("statement", "params"),
    [
        pytest.param(
            "INSERT INTO risk_verdicts (decision_id, verdict) VALUES (%(d)s, 'rejected')",
            {},
            id="rejected-without-rule",
        ),
        pytest.param(
            "INSERT INTO risk_verdicts (decision_id, verdict) VALUES (%(d)s, 'approved')",
            {},
            id="approved-without-order",
        ),
        pytest.param(
            "INSERT INTO risk_verdicts (decision_id, verdict, approved_order) "
            "VALUES (%(d)s, 'approved', '[1, 2]'::jsonb)",
            {},
            id="approved-order-not-object",
        ),
        pytest.param(
            "INSERT INTO risk_verdicts (decision_id, verdict, rejection_rule, approved_order) "
            "VALUES (%(d)s, 'approved', 'x', %(order)s::jsonb)",
            {"order": APPROVED_ORDER},
            id="approved-with-rejection-rule",
        ),
        pytest.param(
            "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, "
            "quote_at_decision) VALUES ('AAPL', 'no_action', 5, 'r', 1)",
            {},
            id="decision-no_action",
        ),
        pytest.param(
            "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, "
            "quote_at_decision) VALUES ('AAPL', 'buy', 101, 'r', 1)",
            {},
            id="decision-size-101",
        ),
        pytest.param(
            "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, "
            "quote_at_decision) VALUES ('AAPL', 'buy', 5, 'r', 0)",
            {},
            id="decision-quote-0",
        ),
        pytest.param(
            "INSERT INTO orders (id, risk_verdict_id, verdict, status) "
            "VALUES ('x', gen_random_uuid(), 'rejected', 'submitted')",
            {},
            id="order-verdict-rejected",
        ),
        pytest.param(
            "INSERT INTO orders (id, risk_verdict_id, status) "
            "VALUES ('x', gen_random_uuid(), 'pending')",
            {},
            id="order-unknown-status",
        ),
    ],
)
def test_invalid_chain_rows_rejected_by_check(conn, statement, params):
    decision = insert_decision(conn, [insert_report(conn)])
    assert sqlstate_of(conn, statement, {"d": decision, **params}) == CHECK_VIOLATION
