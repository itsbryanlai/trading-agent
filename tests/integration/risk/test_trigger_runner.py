"""ADR 0013: the gate evaluates Execution's stop-loss triggers in its own process."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from tests.integration.helpers import as_role
from tests.integration.risk.conftest import NOW, REPO_CONFIG, insert_position, seed_open_day
from trading_agent.risk import runner, service


def _trigger(conn, symbol="AAPL", price="150", observed_at=NOW):
    return conn.execute(
        "INSERT INTO stop_loss_triggers (symbol, observed_price, observed_at) "
        "VALUES (%s, %s, %s) RETURNING id",
        (symbol, price, observed_at),
    ).fetchone()["id"]


def _verdict(conn, trigger):
    return conn.execute(
        "SELECT verdict, approved_order FROM risk_verdicts WHERE stop_loss_trigger_id = %s",
        (trigger,),
    ).fetchone()


def _pass(conn, config=REPO_CONFIG):
    with as_role(conn, "ta_risk_gate"):
        return runner.evaluate_pending_triggers(conn, NOW, config)


def test_todays_unevaluated_triggers_get_verdicts(conn):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    breach = _trigger(conn, price="150")
    not_breach = _trigger(conn, price="170")

    assert _pass(conn) == 2

    assert _verdict(conn, breach)["verdict"] == "approved"
    assert _verdict(conn, breach)["approved_order"]["qty"] == 50
    assert _verdict(conn, not_breach)["verdict"] == "rejected"


def test_a_second_pass_evaluates_nothing(conn):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    _trigger(conn)
    _pass(conn)
    assert _pass(conn) == 0
    assert conn.execute("SELECT count(*) AS n FROM risk_verdicts").fetchone()["n"] == 1


def test_a_trigger_from_an_earlier_day_is_left_alone(conn, caplog):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    old = _trigger(conn, observed_at=datetime(2026, 9, 25, 15, 0, tzinfo=UTC))
    with caplog.at_level(logging.WARNING):
        assert _pass(conn) == 0
    assert _verdict(conn, old) is None and "not today" in caplog.text


def test_a_bad_config_is_logged_and_writes_nothing(conn, tmp_path, caplog):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    trigger = _trigger(conn)
    with caplog.at_level(logging.ERROR):
        assert _pass(conn, tmp_path / "missing.yaml") == 0
    assert _verdict(conn, trigger) is None and "risk config invalid" in caplog.text


def test_one_failing_trigger_does_not_block_the_others(conn, monkeypatch, caplog):
    seed_open_day(conn)
    insert_position(conn, qty=50, avg_entry="200")
    poisoned, fine = _trigger(conn), _trigger(conn, price="160")
    real = service.evaluate_stop_loss_trigger

    def flaky(conn_, trigger_id, **kwargs):
        if trigger_id == poisoned:
            raise RuntimeError("poisoned trigger")
        return real(conn_, trigger_id, **kwargs)

    monkeypatch.setattr(runner, "evaluate_stop_loss_trigger", flaky)
    with caplog.at_level(logging.ERROR):
        assert _pass(conn) == 1
    assert _verdict(conn, poisoned) is None and _verdict(conn, fine) is not None


def test_a_lost_connection_propagates(conn, monkeypatch):
    import psycopg

    seed_open_day(conn)
    _trigger(conn)

    def lost(*args, **kwargs):
        raise psycopg.OperationalError("gone")

    monkeypatch.setattr(runner, "evaluate_stop_loss_trigger", lost)
    with pytest.raises(psycopg.OperationalError):
        _pass(conn)
