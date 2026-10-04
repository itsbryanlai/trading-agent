"""ADR 0019: the gate's own loop evaluates the Portfolio Manager's decisions."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from tests.integration.helpers import as_role
from tests.integration.risk.conftest import (
    NOW,
    REPO_CONFIG,
    insert_position,
    make_decision,
    seed_open_day,
    verdict_count,
)
from trading_agent.risk import runner


def _verdict(conn, decision):
    return conn.execute(
        "SELECT verdict, rejection_rule FROM risk_verdicts WHERE decision_id = %s", (decision,)
    ).fetchone()


def _pass(conn, config=REPO_CONFIG):
    with as_role(conn, "ta_risk_gate"):
        return runner.evaluate_pending_decisions(conn, NOW, config)


def test_a_buy_written_today_gets_exactly_one_verdict(conn):
    seed_open_day(conn)
    decision = make_decision(conn)

    assert _pass(conn) == 1

    assert _verdict(conn, decision)["verdict"] == "approved"
    assert verdict_count(conn) == 1


def test_a_hold_gets_no_verdict(conn):
    seed_open_day(conn)
    hold = make_decision(conn, direction="hold")

    assert _pass(conn) == 0

    assert _verdict(conn, hold) is None and verdict_count(conn) == 0


def test_a_decision_from_the_previous_session_is_left_alone_without_a_log_line(conn, caplog):
    seed_open_day(conn)
    old = make_decision(conn, generated_at=datetime(2026, 9, 25, 15, 0, tzinfo=UTC))

    with caplog.at_level(logging.DEBUG):
        assert _pass(conn) == 0

    assert _verdict(conn, old) is None and caplog.records == []


def test_the_new_york_date_decides_which_day_a_decision_belongs_to(conn):
    seed_open_day(conn)
    # 02:00 UTC on the 28th is still the 27th in New York: not today's.
    yesterday_evening = make_decision(conn, generated_at=datetime(2026, 9, 28, 2, 0, tzinfo=UTC))
    # 04:30 UTC on the 28th is 00:30 ET: today's.
    just_after_midnight = make_decision(conn, generated_at=datetime(2026, 9, 28, 4, 30, tzinfo=UTC))

    assert _pass(conn) == 1

    assert _verdict(conn, yesterday_evening) is None
    assert _verdict(conn, just_after_midnight) is not None


def test_a_second_pass_records_nothing_more(conn):
    seed_open_day(conn)
    make_decision(conn)
    make_decision(conn, direction="sell", target="0")
    assert _pass(conn) == 2

    assert _pass(conn) == 0
    assert verdict_count(conn) == 2


def test_a_sixteen_minute_old_quote_is_rejected_decision_stale(conn, caplog):
    seed_open_day(conn)
    decision = make_decision(conn, quote_time=NOW - timedelta(minutes=16))

    with caplog.at_level(logging.INFO):
        assert _pass(conn) == 1

    verdict = _verdict(conn, decision)
    assert verdict["verdict"] == "rejected" and verdict["rejection_rule"] == "decision_stale"
    assert "rejected (decision_stale)" in caplog.text


def test_decisions_are_evaluated_oldest_first(conn, caplog):
    seed_open_day(conn)
    later = make_decision(conn, generated_at=NOW - timedelta(minutes=1))
    earlier = make_decision(conn, generated_at=NOW - timedelta(minutes=5))

    with caplog.at_level(logging.INFO):
        _pass(conn)

    order = [str(d) for d in (earlier, later)]
    logged = [r.args[0] for r in caplog.records if "decision" in r.getMessage()]
    assert [str(i) for i in logged] == order


def test_one_failing_decision_does_not_block_the_next(conn, monkeypatch, caplog):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    poisoned = make_decision(conn, generated_at=NOW - timedelta(minutes=5))
    fine = make_decision(conn, direction="sell", target="0")
    real = runner.evaluate_decision

    def flaky(conn_, decision_id, **kwargs):
        if decision_id == poisoned:
            raise RuntimeError("poisoned decision")
        return real(conn_, decision_id, **kwargs)

    monkeypatch.setattr(runner, "evaluate_decision", flaky)
    with caplog.at_level(logging.ERROR):
        assert _pass(conn) == 1

    assert _verdict(conn, poisoned) is None and _verdict(conn, fine) is not None
    assert "poisoned" in caplog.text or "failed; carrying on" in caplog.text


def test_an_invalid_risk_config_stops_the_pass_and_writes_nothing(conn, tmp_path, caplog):
    seed_open_day(conn)
    make_decision(conn)
    make_decision(conn, direction="sell", target="0")
    with caplog.at_level(logging.ERROR):
        assert _pass(conn, tmp_path / "missing.yaml") == 0

    assert verdict_count(conn) == 0
    assert caplog.text.count("risk config invalid") == 1


def test_a_lost_connection_propagates(conn, monkeypatch):
    seed_open_day(conn)
    make_decision(conn)

    def lost(*args, **kwargs):
        raise psycopg.OperationalError("gone")

    monkeypatch.setattr(runner, "evaluate_decision", lost)
    with pytest.raises(psycopg.OperationalError):
        _pass(conn)
