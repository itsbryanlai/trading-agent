"""The store's connection handling, with a fake connect (specs/014 tasks T013).

Queries against a real database are in tests/integration/dashboard/test_store_state.py.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

import psycopg
import pytest

from trading_agent.dashboard.store import PostgresStore, StoreBusy, StoreError

URL = "postgresql://ta_dashboard_login:hunter2-fake-password@secret-host.example:5432/prod"


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple | None]] = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self


class FakeConnect:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.urls: list[str] = []
        self.connections: list[FakeConnection] = []
        self.open = 0

    @contextmanager
    def __call__(self, url):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        connection = FakeConnection()
        self.connections.append(connection)
        self.open += 1
        try:
            yield connection
        finally:
            self.open -= 1


def _store(connect, **kwargs) -> PostgresStore:
    return PostgresStore(URL, 10, connect=connect, **kwargs)


def test_a_read_sets_the_transaction_read_only_and_the_statement_timeout_first():
    connect = FakeConnect()
    with _store(connect).read():
        pass
    (connection,) = connect.connections
    assert connection.executed[0] == ("SET TRANSACTION READ ONLY", None)
    assert connection.executed[1] == (
        "SELECT set_config('statement_timeout', %s, true)",
        ("10000",),
    )


def test_the_connection_gets_a_connect_timeout_and_is_closed_after_the_read():
    connect = FakeConnect()
    with _store(connect).read():
        assert connect.open == 1
    assert connect.open == 0
    assert "connect_timeout=5" in connect.urls[0]


def test_a_database_failure_is_a_store_error_naming_only_the_type():
    secret = psycopg.OperationalError(
        f'connection to server at "secret-host.example" failed: {URL}'
    )
    with pytest.raises(StoreError) as caught:
        with _store(FakeConnect(error=secret)).read():
            pass
    text = str(caught.value) + repr(caught.value)
    assert "OperationalError" in text
    for leak in ("secret-host", "hunter2", "ta_dashboard_login", "connection to server"):
        assert leak not in text


def test_a_failure_inside_the_read_block_is_wrapped_too():
    with pytest.raises(StoreError) as caught:
        with _store(FakeConnect()).read():
            raise psycopg.errors.ReadOnlySqlTransaction("cannot execute INSERT secret-table")
    assert str(caught.value) == "database: ReadOnlySqlTransaction"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_an_error_that_is_not_the_databases_passes_through():
    with pytest.raises(ZeroDivisionError):
        with _store(FakeConnect()).read():
            raise ZeroDivisionError


def test_the_store_never_shows_its_url():
    store = _store(FakeConnect())
    assert "hunter2" not in repr(store) and "hunter2" not in str(store)


def test_a_fifth_concurrent_read_waits_then_raises_busy():
    connect = FakeConnect()
    store = _store(connect, acquire_timeout_seconds=0.05)
    release = threading.Event()

    def hold() -> None:
        with store.read():
            release.wait(timeout=5)

    threads = [threading.Thread(target=hold) for _ in range(4)]
    for thread in threads:
        thread.start()
    # Four threads hold a connection each; wait until they all do.
    for _ in range(100):
        if connect.open == 4:
            break
        threading.Event().wait(0.01)
    assert connect.open == 4
    with pytest.raises(StoreBusy):
        with store.read():
            pytest.fail("a fifth connection must not open")
    assert isinstance(StoreBusy("x"), StoreError)  # the same 503 page
    assert connect.open == 4
    release.set()
    for thread in threads:
        thread.join(timeout=5)
    with store.read():  # slots come back
        pass


def test_a_slot_is_given_back_after_a_failure():
    store = _store(
        FakeConnect(error=psycopg.OperationalError("down")), acquire_timeout_seconds=0.05
    )
    for _ in range(10):
        with pytest.raises(StoreError) as caught:
            with store.read():
                pass
        assert not isinstance(caught.value, StoreBusy)


def test_the_cap_is_four_by_default():
    connect = FakeConnect()
    store = _store(connect, acquire_timeout_seconds=0.01)
    contexts = [store.read() for _ in range(4)]
    for context in contexts:
        context.__enter__()
    with pytest.raises(StoreBusy):
        with store.read():
            pass
    for context in contexts:
        context.__exit__(None, None, None)
