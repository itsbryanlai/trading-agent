"""US4: the log lines that explain a missing-data rejection
(contracts/reference-data-interface.md "Log lines"; FR-023-FR-025)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import pytest

from tests.unit.reference.support import make_job
from trading_agent.reference.provider import KeyRejected, ProviderUnavailable
from trading_agent.reference.symbols import Candidate

NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)
OPEN = datetime(2026, 9, 28, 13, 30, tzinfo=UTC)


@pytest.fixture
def logs(caplog):
    # FR-023 (no key or connection string in logs) is tested where the key and URL
    # actually reach the code: test_finnhub_adapter.py and test_main.py.
    caplog.set_level(logging.DEBUG, logger="trading_agent.reference")
    return caplog


def messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_summary_line_and_one_warning_per_failure(logs):
    job, fake, _, _ = make_job(["AAA", "BBB", "CCC"])
    fake.fail("get_quote", "BBB", error=ProviderUnavailable("down"))
    job.tick(NOW)
    info = messages(logs, logging.INFO)
    assert info == [
        "reference: day=2026-09-28 candidates=3 recorded=2 already=0 failed=1 deferred=0"
    ]
    warnings = messages(logs, logging.WARNING)
    assert len(warnings) == 1
    assert re.fullmatch(
        r"reference: BBB failed: provider_unavailable; next attempt at 2026-09-28T12:35:00\+00:00",
        warnings[0],
    )


def test_quiet_when_there_is_nothing_to_do(logs):
    job, _, _, _ = make_job(["AAA"])
    job.tick(NOW)
    logs.clear()
    job.tick(NOW + timedelta(minutes=1))
    assert logs.records == []


def test_invalid_symbols_are_logged(logs):
    job, _, _, _ = make_job([], candidates=[Candidate("bad$", "position", None, None)])
    job.tick(NOW)
    assert any("'bad$' failed: invalid_symbol" in m for m in messages(logs, logging.WARNING))


def test_a_symbol_cannot_forge_a_log_line(logs):
    forged = "X\nreference: day=2026-09-28 candidates=0 recorded=999"
    job, _, _, _ = make_job([], candidates=[Candidate(forged, "position", None, None)])
    job.tick(NOW)
    job.tick(OPEN)
    assert logs.records
    for record in logs.records:
        assert "\n" not in record.getMessage()


def test_open_warning_names_the_missing_once(logs):
    job, fake, _, _ = make_job(["AAA", "BBB"])
    fake.fail("get_quote", "BBB", error=ProviderUnavailable("down"))
    job.tick(NOW)
    logs.clear()
    job.tick(OPEN)
    job.tick(OPEN + timedelta(minutes=1))
    at_open = [m for m in messages(logs, logging.WARNING) if "at open" in m]
    assert at_open == ["reference: at open, no data for: BBB"]


def test_no_open_warning_when_everything_is_recorded(logs):
    job, _, _, _ = make_job(["AAA"])
    job.tick(NOW)
    job.tick(OPEN)
    assert not any("at open" in m for m in messages(logs, logging.WARNING))


def test_key_rejection_line(logs):
    job, fake, _, _ = make_job(["AAA"])
    fake.fail("get_profile", "AAA", error=KeyRejected("HTTP 401"))
    job.tick(NOW)
    assert messages(logs, logging.ERROR) == [
        "reference: market-data key rejected; skipping this run, retrying at "
        "2026-09-28T12:45:00+00:00"
    ]
