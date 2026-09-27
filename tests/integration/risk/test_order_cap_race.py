"""FR-010 + G10: two concurrent evaluations can't both take the last order slot.

This test must commit (two separate connections can't see each other's
rolled-back work), so it runs on its own fresh database.
"""

from __future__ import annotations

import json
import threading

import psycopg
from psycopg.rows import dict_row

from tests.integration.risk.conftest import (
    INTRADAY,
    NOW,
    PRE_OPEN,
    REPO_CONFIG,
    TODAY,
    insert_reference,
    insert_snapshot,
    make_decision,
)
from trading_agent.risk.service import evaluate_decision
from trading_agent.storage.migrate import apply_migrations

_INCREASE = json.dumps(
    {
        "symbol": "X",
        "side": "buy",
        "qty": 1,
        "order_type": "limit",
        "limit_price": "1.00",
        "time_in_force": "day",
        "trading_day": TODAY.isoformat(),
        "exposure": "increase",
        "trims": [],
        "source": "decision",
    }
)


def _seed(url: str) -> list[str]:
    with psycopg.connect(url, row_factory=dict_row) as admin:
        insert_snapshot(admin, PRE_OPEN)
        insert_snapshot(admin, INTRADAY)
        for n in range(4):  # four exposure-increasing approvals already today
            decision = make_decision(admin, symbol=f"OLD{n}")
            admin.execute(
                "INSERT INTO risk_verdicts (decision_id, verdict, approved_order, trading_day, "
                "config_version) VALUES (%s, 'approved', %s::jsonb, %s, 't')",
                (decision, _INCREASE, TODAY),
            )
        racers = []
        for symbol in ("AAPL", "MSFT"):
            insert_reference(admin, symbol=symbol)
            racers.append(make_decision(admin, symbol=symbol))
        admin.commit()
    return racers


def test_only_one_of_two_concurrent_buys_takes_the_last_slot(make_database):
    url = make_database()
    apply_migrations(url)
    racers = _seed(url)

    barrier = threading.Barrier(len(racers))
    verdicts: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(decision_id: str) -> None:
        try:
            with psycopg.connect(url) as conn:
                conn.execute("SET ROLE ta_risk_gate")
                conn.commit()
                barrier.wait(timeout=10)
                verdicts[decision_id] = evaluate_decision(
                    conn, decision_id, now=NOW, config_path=REPO_CONFIG
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(d,)) for d in racers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    outcomes = sorted("approved" if v.approved else v.rejection_rule for v in verdicts.values())
    assert outcomes == ["approved", "daily_order_cap"]
