"""The control writer with a fake connection (specs/014 tasks T019). Real roles: integration."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
import pytest

from trading_agent.dashboard import control
from trading_agent.dashboard.control import ControlError, ControlWriter
from trading_agent.storage.db import ConfigError

URL = "postgresql://ta_dashboard_control_login:MARKER-PASSWORD@secret-host.example:5432/prod"


class Connection:
    def __init__(self, rowcount: int, fail_on: str | None = None) -> None:
        self.rowcount = rowcount
        self.fail_on = fail_on
        self.executed: list[tuple[str, object]] = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if self.fail_on and self.fail_on in query:
            raise psycopg.errors.QueryCanceled("canceling statement MARKER-DETAIL")
        return SimpleNamespace(rowcount=self.rowcount, fetchone=lambda: None, fetchall=lambda: [])


class Connect:
    def __init__(self, rowcount=1, fail_on=None, open_error=None):
        self.rowcount, self.fail_on, self.open_error = rowcount, fail_on, open_error
        self.events: list[str] = []
        self.connection: Connection | None = None
        self.urls: list[str] = []

    @contextmanager
    def __call__(self, url):
        self.urls.append(url)
        if self.open_error:
            raise self.open_error
        self.connection = Connection(self.rowcount, self.fail_on)
        self.events.append("open")
        try:
            yield self.connection
            self.events.append("commit")
        except Exception:
            self.events.append("rollback")
            raise
        finally:
            self.events.append("close")


def _writer(connect) -> ControlWriter:
    return ControlWriter(URL, connect=connect)


def test_the_statement_is_exactly_the_contracts():
    assert control.UPDATE == (
        "UPDATE system_state SET trading_paused = %(target)s, updated_at = now() "
        "WHERE trading_paused = %(expected)s"
    )


@pytest.mark.parametrize(("expected", "target"), [(False, True), (True, False)])
def test_one_row_is_written_in_one_transaction_with_the_timeouts_first(expected, target):
    connect = Connect(rowcount=1)
    assert _writer(connect).set_paused(expected, target) == "written"
    assert connect.connection.executed == [
        ("SET LOCAL lock_timeout = '5s'", None),
        ("SET LOCAL statement_timeout = '10s'", None),
        (control.UPDATE, {"target": target, "expected": expected}),
    ]
    assert connect.events == ["open", "commit", "close"]
    assert "connect_timeout=5" in connect.urls[0]


def test_zero_rows_is_stale_and_changes_nothing():
    connect = Connect(rowcount=0)
    assert _writer(connect).set_paused(False, True) == "stale"
    assert connect.events == ["open", "commit", "close"]


@pytest.mark.parametrize("count", [2, 3, 100])
def test_more_than_one_row_raises_and_rolls_back(count):
    connect = Connect(rowcount=count)
    with pytest.raises(ControlError) as caught:
        _writer(connect).set_paused(False, True)
    assert connect.events == ["open", "rollback", "close"]
    assert "MARKER" not in str(caught.value)


@pytest.mark.parametrize("fail_on", ["lock_timeout", "statement_timeout", "UPDATE"])
def test_a_database_failure_is_a_control_error_naming_only_the_type(fail_on):
    connect = Connect(fail_on=fail_on)
    with pytest.raises(ControlError) as caught:
        _writer(connect).set_paused(False, True)
    assert str(caught.value) == "database: QueryCanceled"
    assert caught.value.__cause__ is None
    assert connect.events == ["open", "rollback", "close"]


def test_a_connection_failure_does_not_leak_the_url():
    error = psycopg.OperationalError(f"connection failed: {URL}")
    with pytest.raises(ControlError) as caught:
        _writer(Connect(open_error=error)).set_paused(False, True)
    assert str(caught.value) == "database: OperationalError"
    assert "MARKER" not in repr(caught.value)


def test_the_writer_never_shows_its_url():
    writer = _writer(Connect())
    assert "MARKER" not in repr(writer) and "MARKER" not in str(writer)


def test_from_env_reads_the_variable_and_refuses_when_absent():
    writer = ControlWriter.from_env({control.VARIABLE: URL}, connect=Connect())
    assert writer.set_paused(False, True) == "written"
    for environ in ({}, {control.VARIABLE: ""}):
        with pytest.raises(ConfigError) as caught:
            ControlWriter.from_env(environ)
        assert control.VARIABLE in str(caught.value) and "MARKER" not in str(caught.value)


def test_control_login_configured_names_the_variable_only_when_missing():
    assert control.control_login_configured({control.VARIABLE: URL}) is None
    assert control.control_login_configured({}) == control.VARIABLE
    assert control.control_login_configured({control.VARIABLE: ""}) == control.VARIABLE
