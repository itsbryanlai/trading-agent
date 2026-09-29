"""US2 end to end: a failure writes nothing for that symbol today, never carries
yesterday forward, and never blocks an exit (SC-004, SC-006)."""

from __future__ import annotations

from datetime import date, timedelta

from tests.integration.helpers import as_role
from tests.integration.reference.conftest import (
    GATE_NOW,
    JOB_NOW,
    TODAY,
    Harness,
    add_position,
    rows_for,
)
from tests.integration.risk.conftest import (
    REPO_CONFIG,
    insert_reference,
    insert_snapshot,
    make_decision,
)
from tests.integration.storage.chain import insert_trigger
from trading_agent.reference.provider import ProviderUnavailable
from trading_agent.risk.rules import UNIVERSE_NO_REFERENCE_DATA
from trading_agent.risk.service import evaluate_decision, evaluate_stop_loss_trigger

FRIDAY = date(2026, 9, 25)


def _open_day(conn):
    insert_snapshot(conn, JOB_NOW)
    insert_snapshot(conn, GATE_NOW - timedelta(minutes=15))


def _only_today_or_seeded(conn):
    days = {r["trading_day"] for r in conn.execute("SELECT trading_day FROM instrument_reference")}
    assert days <= {TODAY, FRIDAY}


def test_failing_provider_does_not_carry_yesterday_forward(conn):
    _open_day(conn)
    insert_reference(conn, symbol="AAPL", day=FRIDAY)  # Friday's row exists
    decision = make_decision(conn, symbol="AAPL")
    harness = Harness(conn)
    harness.fake.add("AAPL")
    harness.fake.fail("get_quote", "AAPL", error=ProviderUnavailable("down"))
    harness.tick(JOB_NOW)

    assert "AAPL" not in rows_for(conn)
    with as_role(conn, "ta_risk_gate"):
        verdict = evaluate_decision(conn, decision, now=GATE_NOW, config_path=REPO_CONFIG)
    assert verdict.rejection_rule == UNIVERSE_NO_REFERENCE_DATA
    _only_today_or_seeded(conn)


def test_implausible_values_write_no_row(conn):
    add_position(conn, "ODD")
    harness = Harness(conn)
    # $100m company trading $500m a day: a unit error, not a stock.
    harness.fake.add(
        "ODD", market_cap_millions="100", avg_volume_10d_millions="10", previous_close="50"
    )
    harness.tick(JOB_NOW)
    assert rows_for(conn) == {}


def test_total_outage_writes_nothing_and_exits_still_pass_the_gate(conn):
    _open_day(conn)
    add_position(conn, "AAPL", qty=50, avg_entry="200")
    harness = Harness(conn)
    harness.fake.add("AAPL")
    harness.fake.fail("list_us_symbols", error=ProviderUnavailable("down"))
    for minute in range(0, 30):
        harness.tick(JOB_NOW + timedelta(minutes=minute))
    assert rows_for(conn) == {}

    with as_role(conn, "ta_execution"):
        trigger = insert_trigger(conn, symbol="AAPL", observed="150")
    with as_role(conn, "ta_risk_gate"):
        verdict = evaluate_stop_loss_trigger(conn, trigger, now=GATE_NOW, config_path=REPO_CONFIG)
    assert verdict.approved
    assert verdict.order.qty == 50 and verdict.order.order_type == "market"
    _only_today_or_seeded(conn)
