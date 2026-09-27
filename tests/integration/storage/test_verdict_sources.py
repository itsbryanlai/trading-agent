"""Migration 0006: a verdict comes from exactly one of a decision or a stop-loss trigger."""

from __future__ import annotations

import pytest

from tests.integration.helpers import as_role, sqlstate_of
from tests.integration.storage.chain import (
    insert_decision,
    insert_order,
    insert_report,
    insert_trigger,
    insert_verdict,
)

CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
NOT_NULL_VIOLATION = "23502"

_RAW = (
    "INSERT INTO risk_verdicts (decision_id, stop_loss_trigger_id, verdict, rejection_rule, "
    "trading_day, config_version) VALUES (%s, %s, 'rejected', 'r', current_date, 't')"
)


def test_verdict_for_a_decision_only(conn):
    decision = insert_decision(conn, [insert_report(conn)])
    assert sqlstate_of(conn, _RAW, (decision, None)) is None


def test_verdict_for_a_trigger_only(conn):
    assert sqlstate_of(conn, _RAW, (None, insert_trigger(conn))) is None


def test_verdict_with_both_or_neither_source_rejected(conn):
    decision = insert_decision(conn, [insert_report(conn)])
    trigger = insert_trigger(conn)
    assert sqlstate_of(conn, _RAW, (decision, trigger)) == CHECK_VIOLATION
    assert sqlstate_of(conn, _RAW, (None, None)) == CHECK_VIOLATION


def test_second_verdict_for_the_same_trigger_rejected(conn):
    trigger = insert_trigger(conn)
    insert_verdict(conn, None, approved=False, trigger_id=trigger)
    assert sqlstate_of(conn, _RAW, (None, trigger)) == UNIQUE_VIOLATION


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO risk_verdicts (decision_id, verdict, rejection_rule, config_version) "
        "VALUES (%s, 'rejected', 'r', 't')",
        "INSERT INTO risk_verdicts (decision_id, verdict, rejection_rule, trading_day) "
        "VALUES (%s, 'rejected', 'r', current_date)",
    ],
    ids=["missing-trading_day", "missing-config_version"],
)
def test_trading_day_and_config_version_required(conn, statement):
    decision = insert_decision(conn, [insert_report(conn)])
    assert sqlstate_of(conn, statement, (decision,)) == NOT_NULL_VIOLATION


def test_order_can_reference_a_triggers_approved_verdict(conn):
    verdict = insert_verdict(conn, None, approved=True, trigger_id=insert_trigger(conn))
    with as_role(conn, "ta_execution"):
        order = insert_order(conn, verdict, side="sell")
    assert order == f"2026-09-28-AAPL-sell-{str(verdict)[:8]}"


def test_invalid_trigger_and_reference_rows_rejected(conn):
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO stop_loss_triggers (symbol, observed_price) VALUES ('AAPL', 0)",
        )
        == CHECK_VIOLATION
    )
    assert (
        sqlstate_of(
            conn,
            "INSERT INTO instrument_reference (symbol, trading_day, security_type, exchange_mic, "
            "market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd) "
            "VALUES ('AAPL', current_date, 'warrant', 'XNAS', 1, 1, 1)",
        )
        == CHECK_VIOLATION
    )
