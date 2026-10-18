"""Every read query the dashboard runs (contracts/queries.md; research D12).

One connection per request through `storage.db.connect`, one `READ ONLY` transaction with a
statement timeout, constant SQL with bound parameters. The login can't write (ADR 0004); the
transaction is read-only as well, as defence in depth. At most four connections are open at
once across pages and the health check, so a flood can't use up the shared database's
connections; a request that can't get one in time is `StoreBusy`.

Errors carry the exception's type only: never the connection string or a database message.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime

import psycopg
from psycopg.conninfo import make_conninfo

from trading_agent.storage import db

MAX_CONNECTIONS = 4
ACQUIRE_TIMEOUT_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 5
# "No lower bound" for `all`: the contract's `taken_at >= :since` keeps its exact shape.
NO_LOWER_BOUND = datetime(1, 1, 1, tzinfo=UTC)

STATE = """
SELECT s.trading_paused, s.trading_paused_changed_at,
       e.daily_loss_halt_active, e.current_trading_date
FROM system_state s CROSS JOIN system_state_effective e
"""
POSITIONS = "SELECT symbol, qty, avg_entry_price, updated_at FROM positions ORDER BY symbol"
LATEST_SNAPSHOT = """
SELECT taken_at, equity, cash, buying_power
FROM account_snapshots ORDER BY taken_at DESC LIMIT 1
"""
EQUITY_POINTS = """
SELECT taken_at, equity FROM account_snapshots
WHERE taken_at >= %(since)s ORDER BY taken_at
"""
EQUITY_DAILY = """
SELECT DISTINCT ON ((taken_at AT TIME ZONE 'America/New_York')::date) taken_at, equity
FROM account_snapshots WHERE taken_at >= %(since)s
ORDER BY (taken_at AT TIME ZONE 'America/New_York')::date, taken_at DESC
"""
LATEST_PM_RUN = """
SELECT outcome, detail, slot_key, COALESCE(started_at, slot_at) AS at
FROM orchestrator_runs WHERE agent = 'portfolio_manager'
ORDER BY COALESCE(started_at, slot_at) DESC NULLS LAST, id DESC LIMIT 1
"""
PING = "SELECT 1"


class StoreError(Exception):
    """A database failure. The message is the exception's type, nothing else."""


class StoreBusy(StoreError):
    """No store connection was free in time."""


class Reader:
    """The queries of one request, all inside one read-only transaction."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    def _all(self, query: str, params: dict | None = None) -> list[dict]:
        return self._connection.execute(query, params).fetchall()

    def _one(self, query: str, params: dict | None = None) -> dict | None:
        return self._connection.execute(query, params).fetchone()

    def state(self) -> dict | None:
        return self._one(STATE)

    def positions(self) -> list[dict]:
        return self._all(POSITIONS)

    def latest_snapshot(self) -> dict | None:
        return self._one(LATEST_SNAPSHOT)

    def equity_points(self, since: datetime | None, daily: bool) -> list[dict]:
        query = EQUITY_DAILY if daily else EQUITY_POINTS
        return self._all(query, {"since": NO_LOWER_BOUND if since is None else since})

    def latest_pm_run(self) -> dict | None:
        """The orchestrator's evidence for a resume (ADR 0028)."""
        return self._one(LATEST_PM_RUN)

    def ping(self) -> bool:
        return self._one(PING) is not None


class PostgresStore:
    def __init__(
        self,
        url: str,
        statement_timeout_seconds: int,
        *,
        connect: Callable[[str], AbstractContextManager] = db.connect,
        max_connections: int = MAX_CONNECTIONS,
        acquire_timeout_seconds: float = ACQUIRE_TIMEOUT_SECONDS,
    ) -> None:
        self._url = url
        self._timeout_ms = str(statement_timeout_seconds * 1000)
        self._connect = connect
        self._slots = threading.BoundedSemaphore(max_connections)
        self._acquire_timeout = acquire_timeout_seconds

    def __repr__(self) -> str:
        return "PostgresStore(<url hidden>)"

    __str__ = __repr__

    @contextmanager
    def read(self) -> Iterator[Reader]:
        """One connection, one read-only transaction, closed on exit."""
        if not self._slots.acquire(timeout=self._acquire_timeout):
            raise StoreBusy("database: busy")
        try:
            url = make_conninfo(self._url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
            with self._connect(url) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)", (self._timeout_ms,)
                )
                yield Reader(connection)
        except (psycopg.Error, OSError) as exc:
            raise StoreError(f"database: {type(exc).__name__}") from None
        finally:
            self._slots.release()
