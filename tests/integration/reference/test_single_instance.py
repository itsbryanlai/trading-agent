"""FR-017: only one reference-data job holds the lock at a time."""

from __future__ import annotations

import time
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from trading_agent.reference import __main__ as runner


def _connect(url):
    return psycopg.connect(url, autocommit=True, row_factory=dict_row)


def test_second_job_is_refused_until_the_first_goes_away(database_url):
    first, second = _connect(database_url), _connect(database_url)
    try:
        runner._take_lock(first, timedelta(0), timedelta(seconds=1), lambda s: None)
        with pytest.raises(runner.AnotherJobRunning):
            runner._take_lock(second, timedelta(seconds=2), timedelta(seconds=1), lambda s: None)
        first.close()
        # The server releases a closed session's lock asynchronously: allow it a
        # moment rather than racing it (the test flaked under load).
        runner._take_lock(second, timedelta(seconds=5), timedelta(milliseconds=100), time.sleep)
    finally:
        first.close()
        second.close()
