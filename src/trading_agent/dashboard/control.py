"""The dashboard's one write: set the pause flag (contracts/toggle.md; research D13, ADR 0026 §4).

This is the only module that names the control login's connection variable, and only the toggle
route, `--check` and the entry point import it (a guard test enforces both). A confirmed toggle
opens one connection, runs one conditional `UPDATE`, commits and closes. The login can update
`trading_paused` and `updated_at` and nothing else; the database stamps
`trading_paused_changed_at` itself (ADR 0027).

Errors carry the exception's type only: never the connection string or a database message.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager

import psycopg
from psycopg.conninfo import make_conninfo

from trading_agent.storage import db

VARIABLE = "DASHBOARD_CONTROL_DATABASE_URL"
CONNECT_TIMEOUT_SECONDS = 5

UPDATE = (
    "UPDATE system_state SET trading_paused = %(target)s, updated_at = now() "
    "WHERE trading_paused = %(expected)s"
)
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
STATEMENT_TIMEOUT = "SET LOCAL statement_timeout = '10s'"
LOGIN = """
SELECT current_user AS login, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
FROM pg_roles WHERE rolname = current_user
"""
GROUP_ROLES = """
SELECT rolname FROM pg_roles
WHERE rolname <> current_user AND pg_has_role(current_user, oid, 'MEMBER')
"""


class ControlError(Exception):
    """The write could not be completed. The message is the exception's type, nothing else."""


def control_login_configured(environ: Mapping[str, str]) -> str | None:
    """The variable's name if it is missing or empty, else None. Never reads the value out."""
    return None if environ.get(VARIABLE) else VARIABLE


class ControlWriter:
    def __init__(
        self, url: str, *, connect: Callable[[str], AbstractContextManager] = db.connect
    ) -> None:
        self._url = url
        self._connect = connect

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **kwargs) -> ControlWriter:
        source = os.environ if environ is None else environ
        value = source.get(VARIABLE)
        if not value:
            raise db.ConfigError(f"environment variable {VARIABLE} is not set")
        return cls(value, **kwargs)

    def __repr__(self) -> str:
        return "ControlWriter(<url hidden>)"

    __str__ = __repr__

    def _conninfo(self) -> str:
        return make_conninfo(self._url, connect_timeout=CONNECT_TIMEOUT_SECONDS)

    def set_paused(self, expected: bool, target: bool) -> str:
        """`written` when the flag was `expected` and is now `target`; `stale` when it was not."""
        try:
            with self._connect(self._conninfo()) as connection:
                connection.execute(LOCK_TIMEOUT)
                connection.execute(STATEMENT_TIMEOUT)
                count = connection.execute(
                    UPDATE, {"target": target, "expected": expected}
                ).rowcount
                if count > 1:  # one row by construction; roll back rather than commit it
                    raise ControlError("database: UnexpectedRowCount")
        except ControlError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ControlError(f"database: {type(exc).__name__}") from None
        return "written" if count == 1 else "stale"

    def memberships(self) -> tuple[dict, list[str]]:
        """For `--check`: the login's flags, and every other role it is a member of."""
        try:
            with self._connect(self._conninfo()) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                login = connection.execute(LOGIN).fetchone()
                roles = [r["rolname"] for r in connection.execute(GROUP_ROLES).fetchall()]
        except (psycopg.Error, OSError) as exc:
            raise ControlError(f"database: {type(exc).__name__}") from None
        return login, sorted(roles)
