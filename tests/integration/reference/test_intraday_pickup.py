"""US3: a symbol named during the day is recorded the same day, within minutes
(SC-003); nothing is fetched after the close."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.integration.reference.conftest import (
    JOB_NOW,
    Harness,
    add_decision,
    add_position,
    add_report,
    rows_for,
)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_a_symbol_reported_at_1400_is_recorded_on_the_next_tick(conn):
    add_position(conn, "HELDA")
    harness = Harness(conn)
    harness.fake.add("HELDA")
    harness.fake.add("NEWCO")
    harness.tick(JOB_NOW)
    calls_before = len(harness.fake.calls)

    add_report(conn, "NEWCO", utc(2026, 9, 28, 14, 0), utc(2026, 9, 28, 20, 0))
    harness.tick(utc(2026, 9, 28, 14, 1))
    assert "NEWCO" in rows_for(conn)
    new_calls = harness.fake.calls[calls_before:]
    assert {s for _, _, s in new_calls} == {"NEWCO"}


def test_a_symbol_named_during_a_long_morning_run_jumps_the_queue(conn):
    held = [f"H{chr(65 + i)}{chr(65 + j)}" for i in range(3) for j in range(10)]
    for symbol in held:
        add_position(conn, symbol)
    harness = Harness(conn)
    for symbol in [*held, "NEWCO"]:
        harness.fake.add(symbol)

    harness.tick(JOB_NOW)  # the first ~50 s of a ~3-minute run
    recorded_first = len(rows_for(conn))
    assert 0 < recorded_first < len(held)

    add_decision(conn, "NEWCO", JOB_NOW + timedelta(seconds=30))  # decided today
    harness.tick(JOB_NOW + timedelta(minutes=1))
    rows = rows_for(conn)
    assert "NEWCO" in rows
    assert len(rows) < len(held) + 1  # lower-priority held symbols still waiting


def test_nothing_is_fetched_after_the_close(conn):
    harness = Harness(conn)
    harness.fake.add("LATE")
    add_report(conn, "LATE", utc(2026, 9, 28, 19, 59), utc(2026, 9, 29, 20, 0))
    harness.tick(utc(2026, 9, 28, 20, 0))
    assert harness.fake.calls == [] and rows_for(conn) == {}


def test_nothing_is_fetched_after_an_early_close(conn):
    harness = Harness(conn)
    harness.fake.add("LATE")
    add_report(conn, "LATE", utc(2026, 11, 27, 17, 59), utc(2026, 11, 30, 20, 0))
    harness.tick(utc(2026, 11, 27, 18, 0))
    assert harness.fake.calls == []
