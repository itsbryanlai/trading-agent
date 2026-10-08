"""The journal writer's reads and its upsert, as `ta_journal` (specs/012 T006)."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.integration.helpers import as_role
from tests.integration.storage.chain import (
    insert_decision,
    insert_order,
    insert_refusal,
    insert_trigger,
    insert_verdict,
)
from trading_agent.journal.model import JournalRow
from trading_agent.journal.store import PgJournalStore
from trading_agent.risk import calendar

DAY = date(2026, 10, 9)  # EDT: open 13:30 UTC, close 20:00 UTC
PREVIOUS = date(2026, 10, 8)
OPEN_AT = calendar.open_time(DAY)
CLOSE_AT = calendar.close_time(DAY)
SOURCES = json.dumps([{"title": "t", "url": "https://example.com/x"}])


def utc(day, hour, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def read(conn, day=DAY):
    with as_role(conn, "ta_journal"):
        return PgJournalStore(conn).read(
            day, calendar.open_time(day), calendar.close_time(day), calendar.close_time
        )


def add_report(conn, at, agent="research", symbol="AAPL", direction="buy"):
    if direction == "no_action":
        return conn.execute(
            "INSERT INTO reports (agent, direction, rationale_md, generated_at, expires_at) "
            "VALUES (%s, 'no_action', 'nothing', %s, %s + interval '1 day') RETURNING id",
            (agent, at, at),
        ).fetchone()["id"]
    return conn.execute(
        "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct, "
        "sources, rationale_md, generated_at, expires_at) "
        "VALUES (%s, %s, %s, 3, 4, %s::jsonb, 'secret thesis', %s, %s + interval '1 day') "
        "RETURNING id",
        (agent, symbol, direction, SOURCES, at, at),
    ).fetchone()["id"]


def add_journal(conn, day, equity_close="100000.00", attribution=None):
    conn.execute(
        "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md, "
        "per_agent_attribution) VALUES (%s, 1, %s, 's', %s)",
        (day, equity_close, Jsonb(attribution or {"schema_version": 1, "agents": {}})),
    )


def test_the_window_runs_from_the_previous_close_to_today_s_close(conn):
    add_journal(conn, PREVIOUS)
    ids = {
        "before": add_report(conn, utc(8, 19, 59)),
        "at_previous_close": add_report(conn, utc(8, 20, 0)),
        "just_after": add_report(conn, utc(8, 20, 0, 1)),
        "mid": add_report(conn, utc(9, 15, 0)),
        "at_close": add_report(conn, utc(9, 20, 0)),
        "after": add_report(conn, utc(9, 20, 0, 1)),
    }
    reads = read(conn)
    got = {r["id"] for r in reads.reports}
    assert got == {ids["just_after"], ids["mid"], ids["at_close"]}
    assert reads.window_start == calendar.close_time(PREVIOUS)
    assert [r["generated_at"] for r in reads.reports] == sorted(
        r["generated_at"] for r in reads.reports
    )


def test_the_reports_carry_only_the_columns_the_journal_needs(conn):
    add_journal(conn, PREVIOUS)
    add_report(conn, utc(9, 15))
    row = read(conn).reports[0]
    assert set(row) == {"id", "agent", "generated_at", "symbol", "direction", "suggested_size_pct"}
    assert row["suggested_size_pct"] == Decimal("4.000")


def test_with_no_previous_row_the_window_is_today_in_new_york_time(conn):
    ids = {
        "yesterday_evening": add_report(conn, utc(8, 23, 0)),  # 19:00 ET on the 8th
        "ny_midnight": add_report(conn, utc(9, 4, 0)),  # 00:00 ET on the 9th
        "mid": add_report(conn, utc(9, 15)),
        "after_close": add_report(conn, utc(9, 21, 0)),  # still the 9th in New York
    }
    reads = read(conn)
    assert {r["id"] for r in reads.reports} == {ids["ny_midnight"], ids["mid"]}
    assert reads.previous is None
    assert reads.window_start is None


def test_the_previous_row_is_the_latest_before_today_and_a_later_row_is_flagged(conn):
    add_journal(conn, date(2026, 10, 6), "1.00")
    add_journal(conn, date(2026, 10, 7), "2.00", {"schema_version": 1, "agents": {"x": 1}})
    reads = read(conn)
    assert reads.previous["trading_day"] == date(2026, 10, 7)
    assert reads.previous["equity_close"] == Decimal("2.00")
    assert reads.previous["per_agent_attribution"]["agents"] == {"x": 1}
    assert reads.has_future_row is False
    add_journal(conn, date(2026, 10, 12))
    assert read(conn).has_future_row is True


def test_the_row_for_today_itself_is_neither_previous_nor_future(conn):
    add_journal(conn, DAY)
    reads = read(conn)
    assert reads.previous is None
    assert reads.has_future_row is False


def _chain(conn, when, symbol="AAPL", approved=True):
    report = add_report(conn, utc(9, 15), agent="research", symbol=symbol)
    decision = insert_decision(conn, [report], symbol=symbol)
    conn.execute("UPDATE decisions SET generated_at = %s WHERE id = %s", (when, decision))
    verdict = insert_verdict(conn, decision, approved=approved)
    conn.execute("UPDATE risk_verdicts SET trading_day = %s WHERE id = %s", (DAY, verdict))
    return report, decision, verdict


def test_the_chain_behind_the_windows_reports_and_the_day_is_read(conn):
    add_journal(conn, PREVIOUS)
    report, decision, verdict = _chain(conn, utc(9, 16))
    order = insert_order(conn, verdict)
    conn.execute("UPDATE orders SET status = 'filled', fill_qty = 10 WHERE id = %s", (order,))
    reads = read(conn)
    assert [r["decision_id"] for r in reads.decision_reports] == [decision]
    assert [d["id"] for d in reads.decisions] == [decision]
    assert set(reads.decisions[0]) == {"id", "generated_at", "symbol", "direction"}
    assert [v["id"] for v in reads.verdicts] == [verdict]
    assert set(reads.verdicts[0]) == {
        "id",
        "decision_id",
        "stop_loss_trigger_id",
        "trading_day",
        "verdict",
        "rejection_rule",
    }
    assert reads.orders == [
        {"risk_verdict_id": verdict, "status": "filled", "fill_qty": Decimal("10.0000")}
    ]


def test_decisions_are_the_days_in_new_york_time_plus_those_citing_the_window(conn):
    add_journal(conn, PREVIOUS)
    _, in_window, _ = _chain(conn, utc(9, 16))
    _, late_night, _ = _chain(conn, utc(10, 3, 30))  # 23:30 ET on the 9th
    _, next_day, _ = _chain(conn, utc(10, 4, 0))  # 00:00 ET on the 10th
    # Made today, citing a report from before the window: still today's decision.
    old = add_report(conn, utc(1, 15))
    cited_old = insert_decision(conn, [old])
    conn.execute("UPDATE decisions SET generated_at = %s WHERE id = %s", (utc(7, 15), cited_old))
    ids = {d["id"] for d in read(conn).decisions}
    assert {in_window, late_night, next_day} <= ids  # next_day cites a window report
    assert cited_old not in ids  # cites nothing in the window, made on another day


def test_stop_loss_verdicts_orders_triggers_and_refusals_of_the_day_are_read(conn):
    add_journal(conn, PREVIOUS)
    trigger = insert_trigger(conn)  # 2026-09-28: another day
    today = conn.execute(
        "INSERT INTO stop_loss_triggers (symbol, observed_price, observed_at) "
        "VALUES ('AAPL', 160, %s) RETURNING id",
        (utc(9, 18),),
    ).fetchone()["id"]
    stop_verdict = insert_verdict(conn, None, trigger_id=today)
    conn.execute("UPDATE risk_verdicts SET trading_day = %s WHERE id = %s", (DAY, stop_verdict))
    stop_order = insert_order(conn, stop_verdict, side="sell")
    _, _, refused = _chain(conn, utc(9, 16))
    insert_refusal(conn, refused, "daily_loss_line_crossed")
    conn.execute("UPDATE execution_refusals SET refused_at = %s", (utc(9, 17),))
    reads = read(conn)
    assert [t["id"] for t in reads.triggers] == [today]
    assert trigger not in [t["id"] for t in reads.triggers]
    assert stop_verdict in [v["id"] for v in reads.verdicts]
    assert stop_order and stop_verdict in [o["risk_verdict_id"] for o in reads.orders]
    assert reads.refusals == [{"reason": "daily_loss_line_crossed", "refused_at": utc(9, 17)}]
    assert set(reads.refusals[0]) == {"reason", "refused_at"}


def _snapshot(conn, at, equity):
    conn.execute(
        "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
        "VALUES (%s, %s, 0, 0)",
        (at, equity),
    )


def test_equity_open_is_the_latest_snapshot_at_or_before_the_open_and_close_the_latest(conn):
    _snapshot(conn, utc(8, 19, 0), "999.00")  # yesterday: ignored
    _snapshot(conn, utc(9, 12, 0), "100.00")
    _snapshot(conn, utc(9, 13, 30), "101.00")  # exactly at the open
    _snapshot(conn, utc(9, 14, 0), "102.00")
    _snapshot(conn, utc(9, 19, 30), "103.00")
    reads = read(conn)
    assert reads.snapshot_open == {"taken_at": utc(9, 13, 30), "equity": Decimal("101.00")}
    assert reads.snapshot_close == {"taken_at": utc(9, 19, 30), "equity": Decimal("103.00")}


def test_equity_open_is_the_earliest_of_the_day_when_none_is_before_the_open(conn):
    _snapshot(conn, utc(9, 14, 0), "102.00")
    _snapshot(conn, utc(9, 15, 0), "103.00")
    reads = read(conn)
    assert reads.snapshot_open["equity"] == Decimal("102.00")
    assert reads.snapshot_close["equity"] == Decimal("103.00")


def test_no_snapshot_today_reads_as_none(conn):
    _snapshot(conn, utc(8, 19, 0), "999.00")
    reads = read(conn)
    assert reads.snapshot_open is None
    assert reads.snapshot_close is None


def _row(summary="first", close="101.50"):
    return JournalRow(
        DAY, Decimal("100.00"), Decimal(close), summary, {"schema_version": 1, "agents": {}}
    )


def test_an_upsert_twice_leaves_one_row_with_the_second_values(conn):
    with as_role(conn, "ta_journal"):
        store = PgJournalStore(conn)
        store.upsert(_row("first", "101.50"))
        store.upsert(_row("second", "102.25"))
    rows = conn.execute("SELECT * FROM journal WHERE trading_day = %s", (DAY,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["summary_md"] == "second"
    assert rows[0]["equity_close"] == Decimal("102.25")
    assert rows[0]["equity_open"] == Decimal("100.00")
    assert rows[0]["per_agent_attribution"] == {"schema_version": 1, "agents": {}}
    assert rows[0]["written_at"] is not None


def test_the_store_never_names_text_a_model_or_the_broker_wrote():
    source = Path(__file__).resolve().parents[3] / "src/trading_agent/journal/store.py"
    text = source.read_text()
    for name in (
        "rationale_md",
        "reasoning_md",
        "sources",
        "broker_reason",
        "details",
        "system_state",
    ):
        assert not re.search(rf"\b{name}\b", text), name


class SpyConnection(psycopg.Connection):
    """Records the transaction's isolation level and mode at each statement."""

    seen: list[tuple[str, str]]

    def execute(self, query, params=None, **kwargs):
        if not hasattr(self, "seen"):
            self.seen = []
        cursor = super().execute(query, params, **kwargs)
        with self.cursor() as peek:
            peek.execute("SHOW transaction_isolation")
            isolation = peek.fetchone()["transaction_isolation"]
            peek.execute("SHOW transaction_read_only")
            self.seen.append((isolation, peek.fetchone()["transaction_read_only"]))
        return cursor


def test_all_reads_run_in_one_repeatable_read_read_only_transaction(database_url):
    with SpyConnection.connect(database_url, row_factory=dict_row) as spy:
        PgJournalStore(spy).read(DAY, OPEN_AT, CLOSE_AT, calendar.close_time)
        assert len(spy.seen) >= 10
        assert set(spy.seen) == {("repeatable read", "on")}
        # Back to the connection's own defaults afterwards.
        spy.rollback()
        assert spy.isolation_level is None
        assert spy.read_only is None


@pytest.mark.parametrize("table", ["system_state", "reports", "orders"])
def test_the_role_reads_what_it_needs_and_not_system_state(conn, table):
    from tests.integration.helpers import attempt

    expected = "denied" if table == "system_state" else "allowed"
    assert attempt(conn, "ta_journal", f"SELECT count(*) FROM {table}") == expected
