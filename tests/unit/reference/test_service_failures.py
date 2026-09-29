"""US2 at the service level: every failure withholds that symbol's row, backs off,
and leaves the rest of the job running (FR-005, FR-014, FR-016, FR-019a, D8, D9)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from tests.unit.reference.support import make_job
from trading_agent.reference import normalize as n
from trading_agent.reference.provider import (
    KeyRejected,
    Listing,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)
from trading_agent.reference.symbols import Candidate

NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)
DAY = date(2026, 9, 28)


def minutes(m):
    return NOW + timedelta(minutes=m)


def test_one_failing_symbol_does_not_stop_the_others():
    job, fake, store, _ = make_job(["AAA", "BBB"])
    fake.fail("get_quote", "AAA", error=ProviderUnavailable("down"))
    report = job.tick(NOW)
    assert ("AAA", DAY) not in store.rows and ("BBB", DAY) in store.rows
    assert report.failed == 1 and report.recorded == 1


def test_backoff_schedule_then_recovery():
    job, fake, store, _ = make_job(["AAA"])
    fake.fail("get_quote", "AAA", error=ProviderUnavailable("down"))
    attempts = []
    for m in range(0, 100):
        before = len(fake.calls_for("AAA"))
        job.tick(minutes(m))
        if len(fake.calls_for("AAA")) > before:
            attempts.append(m)
    # First try, then +5, +10, +20, then every +30.
    assert attempts[:6] == [0, 5, 15, 35, 65, 95]
    fake.recover("get_quote", "AAA")
    job.tick(minutes(125))
    assert ("AAA", DAY) in store.rows


def test_backoff_applies_to_normalize_failures_too():
    job, fake, store, _ = make_job(["GONE", "HUGE"])
    fake.listings.pop("GONE")  # not listed
    fake.add("HUGE", market_cap_millions="100", avg_volume_10d_millions="10", previous_close="50")
    job.tick(NOW)
    calls = len(fake.calls)
    report = job.tick(minutes(1))
    assert len(fake.calls) == calls  # neither retried a minute later
    assert report.deferred == 2 and report.failed == 0
    assert store.rows == {}


def test_invalid_symbol_is_logged_once_per_attempt_not_every_tick(caplog):
    job, fake, _, _ = make_job([], candidates=[Candidate("bad$", "position", None, None)])
    with caplog.at_level(logging.WARNING, logger="trading_agent.reference"):
        for m in range(4):
            job.tick(minutes(m))
    lines = [r.getMessage() for r in caplog.records if "bad$" in r.getMessage()]
    assert len(lines) == 1 and "invalid_symbol" in lines[0]
    assert fake.calls_for("bad$") == []


def test_rate_limit_stops_the_tick_without_backing_off_the_symbol():
    job, fake, store, _ = make_job(["AAA", "BBB", "CCC"])
    fake.fail("get_profile", "BBB", error=RateLimited("429"))
    report = job.tick(NOW)
    assert report.stopped == "rate_limited"
    assert fake.calls_for("CCC") == []  # nothing new started after the 429
    fake.recover("get_profile", "BBB")
    job.tick(minutes(1))  # a minute later, not five
    assert {s for s, _ in store.rows} == {"AAA", "BBB", "CCC"}


def test_key_rejected_mid_run_skips_the_run_once_and_waits_fifteen_minutes(caplog):
    job, fake, store, _ = make_job(["AAA", "BBB"])
    fake.fail("get_profile", "AAA", error=KeyRejected("401"))
    with caplog.at_level(logging.INFO, logger="trading_agent.reference"):
        report = job.tick(NOW)
    assert report.stopped == "key_rejected"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "key rejected" in errors[0].getMessage()
    assert not any("AAA failed" in r.getMessage() for r in caplog.records)
    calls = len(fake.calls)
    for m in range(1, 15):
        job.tick(minutes(m))
    assert len(fake.calls) == calls
    fake.recover("get_profile", "AAA")
    job.tick(minutes(15))
    assert {s for s, _ in store.rows} == {"AAA", "BBB"}


def test_symbol_list_unavailable_fetches_nothing_and_retries_next_tick():
    job, fake, store, _ = make_job(["AAA"])
    fake.fail("list_us_symbols", error=ProviderUnavailable("down"))
    report = job.tick(NOW)
    assert report.stopped == "list_unavailable" and store.rows == {}
    assert fake.calls_for("AAA") == []
    fake.recover("list_us_symbols")
    job.tick(minutes(1))
    assert ("AAA", DAY) in store.rows


def test_database_error_on_one_insert_is_that_symbols_failure(caplog):
    job, fake, store, _ = make_job(["AAA", "BBB"])
    store.insert_error["AAA"] = psycopg.errors.CheckViolation("check")
    with caplog.at_level(logging.WARNING, logger="trading_agent.reference"):
        report = job.tick(NOW)
    assert report.failed == 1 and ("BBB", DAY) in store.rows
    assert any(n.DATABASE_ERROR in r.getMessage() for r in caplog.records)


def test_a_lost_connection_propagates():
    job, fake, store, _ = make_job(["AAA"])
    store.insert_error["AAA"] = psycopg.OperationalError("gone")
    with pytest.raises(psycopg.OperationalError):
        job.tick(NOW)


def test_yesterdays_row_is_never_carried_forward():
    job, fake, store, _ = make_job(["AAA"])
    fake.quote_time = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)
    job.tick(datetime(2026, 9, 25, 12, 30, tzinfo=UTC))  # Friday: recorded
    fake.quote_time = datetime(2026, 9, 28, 11, 0, tzinfo=UTC)
    fake.fail("get_quote", "AAA", error=ProviderUnavailable("down"))
    job.tick(NOW)  # Monday: provider down
    assert ("AAA", date(2026, 9, 25)) in store.rows
    assert ("AAA", DAY) not in store.rows


def test_unlisted_and_conflicting_symbols_cost_no_calls():
    job, fake, store, _ = make_job(["GONE", "DUP", "OK"])
    fake.listings.pop("GONE")
    fake.listings["DUP"] = Listing("DUP", None, None, conflicting=True)
    report = job.tick(NOW)
    assert fake.calls_for("GONE") == [] and fake.calls_for("DUP") == []
    assert report.failed == 2 and {s for s, _ in store.rows} == {"OK"}


def test_a_403_on_one_symbol_fails_only_that_symbol(caplog):
    job, fake, store, _ = make_job(["AAA", "BBB"])
    fake.fail("get_profile", "AAA", error=NotPermitted("HTTP 403"))
    with caplog.at_level(logging.WARNING, logger="trading_agent.reference"):
        report = job.tick(NOW)
    assert report.stopped is None and report.failed == 1
    failures = [r.getMessage() for r in caplog.records if "AAA failed" in r.getMessage()]
    assert len(failures) == 1 and f"failed: {n.NOT_PERMITTED};" in failures[0]
    assert {s for s, _ in store.rows} == {"BBB"}
    job.tick(minutes(1))
    assert fake.calls_for("AAA") == ["get_profile"]  # backed off, not retried a minute later


def test_an_unexpected_error_fails_only_that_symbol(caplog):
    job, fake, store, _ = make_job(["AAA", "BBB"])
    fake.fail("get_metrics", "AAA", error=RuntimeError("bug"))
    with caplog.at_level(logging.WARNING, logger="trading_agent.reference"):
        report = job.tick(NOW)
    assert report.failed == 1 and {s for s, _ in store.rows} == {"BBB"}
    assert any(n.INTERNAL_ERROR in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("method", ["recorded_symbols", "read_candidates"])
def test_a_database_read_error_skips_the_tick_without_crashing(method, caplog):
    job, fake, store, _ = make_job(["AAA"])

    def broken(*args):
        raise psycopg.errors.InsufficientPrivilege("denied")

    setattr(store, method, broken)
    with caplog.at_level(logging.ERROR, logger="trading_agent.reference"):
        report = job.tick(NOW)
    assert report.stopped == "database_error" and fake.calls == []
    assert any("database read failed" in r.getMessage() for r in caplog.records)


def test_a_lost_connection_on_read_propagates():
    job, _, store, _ = make_job(["AAA"])

    def gone(*args):
        raise psycopg.OperationalError("gone")

    store.recorded_symbols = gone
    with pytest.raises(psycopg.OperationalError):
        job.tick(NOW)
