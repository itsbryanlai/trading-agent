"""Database connection handling.

No connection pool: each component runs a handful of scheduled sessions per
day, not a request-serving workload, so a pool would add a lifecycle to manage
for no throughput benefit. Each unit of work opens a connection and closes it.

Connection strings are never logged. A missing one is reported by the name of
its environment variable, never its value.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


class ConfigError(Exception):
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"environment variable {name} is not set")
    return value


@contextmanager
def connect(url: str) -> Iterator[psycopg.Connection]:
    """Open a connection; commit on clean exit, roll back on exception."""
    connection = psycopg.connect(url, row_factory=dict_row)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
