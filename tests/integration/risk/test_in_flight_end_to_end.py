"""Feature 009 end to end: while an approval's order is working, deciding the same
target again orders nothing; once it fills, a higher target buys the difference
(ADR 0020, spec US1 and US2)."""

from __future__ import annotations

from tests.integration.helpers import as_role
from tests.integration.risk.conftest import (
    NOW,
    insert_position,
    make_decision,
    seed_open_day,
)
from tests.integration.storage.chain import order_id_for
from trading_agent.risk import rules
from trading_agent.risk.service import evaluate_decision


def _evaluate(conn, repo_config, decision):
    with as_role(conn, "ta_risk_gate"):
        return evaluate_decision(conn, decision, now=NOW, config_path=repo_config)


def test_same_target_while_working_then_the_difference_after_the_fill(conn, repo_config):
    seed_open_day(conn)

    first = _evaluate(conn, repo_config, make_decision(conn, target="5"))
    assert first.approved and first.order.qty == 24

    # Execution places the order; nothing has filled yet.
    first_decision = conn.execute(
        "SELECT id FROM risk_verdicts WHERE verdict = 'approved'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO orders (id, risk_verdict_id, status, limit_price) "
        "VALUES (%s, %s, 'submitted', 202)",
        (order_id_for(first_decision, "buy", "AAPL"), first_decision),
    )

    second = _evaluate(conn, repo_config, make_decision(conn, target="5"))
    assert not second.approved and second.rejection_rule == rules.TARGET_ALREADY_MET

    # The order fills and the position is written; nothing is in flight any more.
    conn.execute(
        "UPDATE orders SET status = 'filled', fill_qty = 24, fill_price = 200 WHERE id = %s",
        (order_id_for(first_decision, "buy", "AAPL"),),
    )
    insert_position(conn, "AAPL", qty=24, avg_entry="200")

    third = _evaluate(conn, repo_config, make_decision(conn, target="7"))
    # $7,000 target less 24 held at the $200 quote: floor(2200 / 202) = 10.
    assert third.approved and third.order.qty == 10
