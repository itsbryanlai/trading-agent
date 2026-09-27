"""Helpers for running statements as a specific ta_* group role."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

import psycopg
from psycopg import sql


def _require_outer_transaction(conn: psycopg.Connection) -> None:
    # Outside a transaction, conn.transaction() would BEGIN/COMMIT for real
    # rather than use a savepoint, and the test's writes would persist.
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError("helper needs an open outer transaction; use the `conn` fixture")


def _set_role(conn: psycopg.Connection, role: str) -> None:
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))


def attempt(
    conn: psycopg.Connection, role: str, statement: str | sql.Composable, params=None
) -> Literal["allowed", "denied"]:
    """Run `statement` as `role` and undo everything, including the role switch.

    SQLSTATE 42501 covers both a missing grant and a row-level security
    violation. Any other error is a broken test, not a denial, so it is raised.
    """
    _require_outer_transaction(conn)
    try:
        with conn.transaction():
            _set_role(conn, role)
            conn.execute(statement, params)
            raise psycopg.Rollback()
    except psycopg.errors.InsufficientPrivilege:
        return "denied"
    return "allowed"


@contextmanager
def as_role(conn: psycopg.Connection, role: str) -> Iterator[psycopg.Connection]:
    """Run the block as `role` inside a savepoint.

    On success the savepoint is released (the block's writes stay visible to the
    rest of the test's transaction) and the role is reset. On error the savepoint
    is rolled back, which also reverts the role.
    """
    _require_outer_transaction(conn)
    with conn.transaction():
        _set_role(conn, role)
        yield conn
        conn.execute("RESET ROLE")


def sqlstate_of(conn: psycopg.Connection, statement: str, params=None) -> str | None:
    """Execute inside a savepoint; return the SQLSTATE it failed with, or None."""
    _require_outer_transaction(conn)
    try:
        with conn.transaction():
            conn.execute(statement, params)
    except psycopg.Error as exc:
        return exc.sqlstate
    return None
