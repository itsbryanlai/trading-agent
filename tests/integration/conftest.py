"""Integration fixtures: a throwaway database per session, migrated from scratch.

Requires TEST_DATABASE_URL pointing at a disposable server whose user is a
superuser (the tests SET ROLE into every ta_* role). Never the Railway database.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from trading_agent.storage.migrate import apply_migrations

_HERE = Path(__file__).resolve().parent


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in Path(item.fspath).resolve().parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def server_url() -> str:
    # Locally, no database means the integration suite is skipped. In CI,
    # TEST_DATABASE_REQUIRED=1 turns that skip into a failure, so a broken
    # database service can't pass as a green run.
    give_up = pytest.fail if os.environ.get("TEST_DATABASE_REQUIRED") == "1" else pytest.skip
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        give_up("TEST_DATABASE_URL is not set; integration suite skipped")
    try:
        with psycopg.connect(url, connect_timeout=5):
            pass
    except psycopg.OperationalError as exc:
        give_up(f"TEST_DATABASE_URL unreachable: {exc}")
    return url


@pytest.fixture(scope="session")
def make_database(server_url: str) -> Iterator[Callable[[], str]]:
    """Factory for fresh, empty databases on the test server; all dropped at the end."""
    created: list[str] = []

    def _make() -> str:
        name = f"ta_test_{secrets.token_hex(6)}"
        with psycopg.connect(server_url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created.append(name)
        return make_conninfo(server_url, dbname=name)

    yield _make

    with psycopg.connect(server_url, autocommit=True) as admin:
        for name in created:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture(scope="session")
def database_url(make_database: Callable[[], str]) -> str:
    url = make_database()
    apply_migrations(url)
    return url


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    """A connection whose work is always rolled back, so tests leave no trace.

    The outer transaction is opened immediately. Without it, the first
    `conn.transaction()` in a test (inside `as_role`/`attempt`) would be a real
    transaction that COMMITs, instead of a savepoint, and leak rows into
    later tests.
    """
    connection = psycopg.connect(database_url, row_factory=dict_row)
    connection.execute("SELECT 1")
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
