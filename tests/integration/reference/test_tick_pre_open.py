"""US1 end to end: before the open, every candidate gets today's row, and the gate
can then judge a buy on its merits (spec US1 independent test)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.integration.helpers import as_role
from tests.integration.reference.conftest import (
    GATE_NOW,
    JOB_NOW,
    TODAY,
    Harness,
    add_decision,
    add_position,
    add_report,
    rows_for,
)
from tests.integration.risk.conftest import (
    REPO_CONFIG,
    insert_snapshot,
    make_decision,
)
from trading_agent.reference.normalize import ReferenceRow
from trading_agent.reference.service import PgReferenceStore
from trading_agent.risk.rules import UNIVERSE_NO_REFERENCE_DATA
from trading_agent.risk.service import evaluate_decision


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def _seed(conn, harness):
    add_position(conn, "HELDA")
    add_position(conn, "HELDB")
    add_report(conn, "RPTA", utc(2026, 9, 25, 15), utc(2026, 9, 28, 20))
    add_report(conn, "RPTB", utc(2026, 9, 25, 16), utc(2026, 9, 25, 20))
    add_decision(conn, "DECA", utc(2026, 9, 25, 17))
    add_report(conn, "STALE", utc(2026, 9, 1, 15), utc(2026, 9, 1, 20))  # outside the window
    for symbol in ("HELDA", "HELDB", "RPTA", "RPTB", "DECA", "SEEDA", "STALE"):
        harness.fake.add(symbol)


def _run_until_quiet(harness, now):
    for _ in range(10):
        report = harness.tick(now)
        if report.recorded == 0:
            return
        now += timedelta(minutes=1)


def test_every_candidate_gets_one_row_for_today(conn):
    harness = Harness(conn, seeds=("SEEDA",))
    _seed(conn, harness)
    _run_until_quiet(harness, JOB_NOW)

    rows = rows_for(conn)
    assert set(rows) == {"HELDA", "HELDB", "RPTA", "RPTB", "DECA", "SEEDA"}
    row = rows["RPTA"]
    assert row["security_type"] == "common_stock"
    assert row["exchange_mic"] == "XNAS"
    assert row["market_cap_usd"] == 3_000_000 * 10**6
    assert row["avg_daily_dollar_volume_usd"] == 25 * 10**6 * 200
    assert row["share_price_usd"] == 200
    assert harness.fake.calls_for("STALE") == []


def test_rerunning_leaves_rows_identical(conn):
    harness = Harness(conn, seeds=("SEEDA",))
    _seed(conn, harness)
    _run_until_quiet(harness, JOB_NOW)
    before = rows_for(conn)
    calls = len(harness.fake.calls)
    again = Harness(conn, seeds=("SEEDA",))  # a restart: fresh memory
    _seed_provider_only(again)
    again.tick(JOB_NOW + timedelta(minutes=30))
    assert rows_for(conn) == before
    assert [c for _, c, s in again.fake.calls if s is not None] == []
    assert len(harness.fake.calls) == calls


def _seed_provider_only(harness):
    for symbol in ("HELDA", "HELDB", "RPTA", "RPTB", "DECA", "SEEDA"):
        harness.fake.add(symbol)


def test_the_gate_can_judge_a_buy_once_the_row_exists(conn):
    insert_snapshot(conn, utc(2026, 9, 28, 12, 0))
    insert_snapshot(conn, utc(2026, 9, 28, 13, 45))
    decision = make_decision(conn, symbol="AAPL", quote="200")
    harness = Harness(conn)
    harness.fake.add("AAPL")  # $3T cap, $5bn/day, $200: passes every universe floor
    _run_until_quiet(harness, JOB_NOW)
    assert "AAPL" in rows_for(conn)

    with as_role(conn, "ta_risk_gate"):
        verdict = evaluate_decision(conn, decision, now=GATE_NOW, config_path=REPO_CONFIG)
    assert verdict.rejection_rule != UNIVERSE_NO_REFERENCE_DATA
    assert verdict.approved, verdict.rejection_rule


def test_no_rows_for_another_day(conn):
    harness = Harness(conn)
    add_position(conn, "HELDA")
    harness.fake.add("HELDA")
    _run_until_quiet(harness, JOB_NOW)
    days = {r["trading_day"] for r in conn.execute("SELECT trading_day FROM instrument_reference")}
    assert days == {TODAY}


def test_store_insert_is_idempotent_when_the_row_already_exists(conn):
    # A row written between the job's "already recorded?" read and its insert
    # (e.g. an overlapping process): the insert is a no-op, not an error (D9).
    first = ReferenceRow(
        "RACE", "common_stock", "XNAS", Decimal("1000"), Decimal("10"), Decimal("5")
    )
    second = ReferenceRow("RACE", "etf", "ARCX", Decimal("9"), Decimal("9"), Decimal("9"))
    store = PgReferenceStore(conn, _allow_savepoints=True)
    with as_role(conn, "ta_reference_data"):
        store.insert(first, TODAY)
        store.insert(second, TODAY)
    row = rows_for(conn)["RACE"]
    assert row["security_type"] == "common_stock" and row["share_price_usd"] == 5
