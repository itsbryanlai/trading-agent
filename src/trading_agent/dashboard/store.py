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
from datetime import UTC, date, datetime

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
ORDER_HISTORY = """
SELECT v.id, v.evaluated_at, v.trading_day, v.verdict, v.rejection_rule, v.approved_order,
       d.id AS decision_id, d.generated_at AS decided_at, d.symbol AS d_symbol,
       d.direction, d.size_pct, d.quote_at_decision, d.quote_time,
       t.id AS trigger_id, t.symbol AS t_symbol, t.observed_price, t.observed_at,
       o.id AS order_id, o.status, o.limit_price, o.fill_qty, o.fill_price,
       o.broker_reason, o.submitted_at,
       r.reason AS refusal_reason, r.details AS refusal_details, r.refused_at
FROM risk_verdicts v
LEFT JOIN decisions d ON d.id = v.decision_id
LEFT JOIN stop_loss_triggers t ON t.id = v.stop_loss_trigger_id
LEFT JOIN orders o ON o.risk_verdict_id = v.id
LEFT JOIN execution_refusals r ON r.risk_verdict_id = v.id
WHERE (%(symbol)s::text IS NULL OR COALESCE(d.symbol, t.symbol) = %(symbol)s::text)
  AND (%(from)s::timestamptz IS NULL OR v.evaluated_at >= %(from)s::timestamptz)
  AND (%(to)s::timestamptz IS NULL OR v.evaluated_at < %(to)s::timestamptz)
ORDER BY v.evaluated_at DESC, v.id DESC
LIMIT %(limit)s OFFSET %(offset)s
"""
_SINCE = "(%(from)s::timestamptz IS NULL OR {col} >= %(from)s::timestamptz)"
_UNTIL = "(%(to)s::timestamptz IS NULL OR {col} < %(to)s::timestamptz)"
REPORTS = f"""
SELECT id, agent, generated_at, symbol, direction, conviction, suggested_size_pct,
       sources, rationale_md
FROM reports
WHERE (%(agent)s::text IS NULL OR agent = %(agent)s::text)
  AND (%(symbol)s::text IS NULL OR symbol = %(symbol)s::text)
  AND {_SINCE.format(col="generated_at")} AND {_UNTIL.format(col="generated_at")}
ORDER BY generated_at DESC, id DESC LIMIT %(limit)s
"""
DECISIONS = f"""
SELECT d.id, d.generated_at, d.symbol, d.direction, d.size_pct, d.reasoning_md,
       d.quote_at_decision, d.quote_time,
       COALESCE(array_agg(dr.report_id) FILTER (WHERE dr.report_id IS NOT NULL), '{{}}')
           AS report_ids
FROM decisions d LEFT JOIN decision_reports dr ON dr.decision_id = d.id
WHERE (%(symbol)s::text IS NULL OR d.symbol = %(symbol)s::text)
  AND {_SINCE.format(col="d.generated_at")} AND {_UNTIL.format(col="d.generated_at")}
GROUP BY d.id ORDER BY d.generated_at DESC, d.id DESC LIMIT %(limit)s
"""
RUNS = f"""
SELECT id, agent, trading_day, reason, slot_key, slot_at, started_at, finished_at,
       outcome, detail
FROM orchestrator_runs
WHERE (%(agent)s::text IS NULL OR agent = %(agent)s::text)
  AND {_SINCE.format(col="COALESCE(started_at, slot_at)")}
  AND {_UNTIL.format(col="COALESCE(started_at, slot_at)")}
  AND (%(problems)s::boolean = false
       OR outcome IN ('failed', 'timed_out', 'skipped', 'interrupted'))
ORDER BY COALESCE(started_at, slot_at) DESC NULLS LAST, id DESC LIMIT %(limit)s
"""
REPORT_TIMES = "SELECT id, generated_at FROM reports WHERE id = ANY(%(ids)s::uuid[])"
JOURNAL_PAGE = """
SELECT trading_day, equity_open, equity_close, summary_md, per_agent_attribution
FROM journal WHERE trading_day >= %(since)s
ORDER BY trading_day DESC LIMIT %(limit)s OFFSET %(offset)s
"""
JOURNAL_RANGE = """
SELECT trading_day, equity_close, per_agent_attribution FROM journal
WHERE trading_day >= %(since)s ORDER BY trading_day
"""
NO_EARLIEST_DAY = date.min
LOGIN = """
SELECT current_user AS login, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
FROM pg_roles WHERE rolname = current_user
"""
GROUP_ROLES = """
SELECT rolname FROM pg_roles
WHERE rolname LIKE 'ta\\_%' AND rolname NOT LIKE '%\\_login'
  AND pg_has_role(current_user, oid, 'MEMBER')
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

    def orders(
        self,
        symbol: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        """One row per risk verdict, newest first; `until` is exclusive."""
        params = {"symbol": symbol, "from": since, "to": until, "limit": limit, "offset": offset}
        return self._all(ORDER_HISTORY, params)

    def reports(self, agent, symbol, since, until, limit: int) -> list[dict]:
        params = {"agent": agent, "symbol": symbol, "from": since, "to": until, "limit": limit}
        return self._all(REPORTS, params)

    def decisions(self, symbol, since, until, limit: int) -> list[dict]:
        params = {"symbol": symbol, "from": since, "to": until, "limit": limit}
        return self._all(DECISIONS, params)

    def runs(self, agent, since, until, problems: bool, limit: int) -> list[dict]:
        params = {
            "agent": agent,
            "from": since,
            "to": until,
            "problems": problems,
            "limit": limit,
        }
        return self._all(RUNS, params)

    def report_times(self, ids: list) -> dict:
        """When each of these reports was written, so a decision can link to its day."""
        if not ids:
            return {}
        rows = self._all(REPORT_TIMES, {"ids": [str(i) for i in ids]})
        return {str(row["id"]): row["generated_at"] for row in rows}

    def journal_page(self, since: date | None, limit: int, offset: int) -> list[dict]:
        """Day rows, newest first; `since` None is the whole journal."""
        params = {"since": NO_EARLIEST_DAY if since is None else since}
        return self._all(JOURNAL_PAGE, {**params, "limit": limit, "offset": offset})

    def journal_range(self, since: date | None) -> list[dict]:
        """Every row in the range, oldest first, for the chart and the usage totals."""
        return self._all(JOURNAL_RANGE, {"since": NO_EARLIEST_DAY if since is None else since})

    def latest_pm_run(self) -> dict | None:
        """The orchestrator's evidence for a resume (ADR 0028)."""
        return self._one(LATEST_PM_RUN)

    def memberships(self) -> tuple[dict, list[str]]:
        """For `--check`: the login's flags, and the `ta_*` group roles it belongs to."""
        login = self._one(LOGIN)
        roles = [row["rolname"] for row in self._all(GROUP_ROLES)]
        return login, sorted(roles)

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
            raise StoreBusy("database: StoreBusy")
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
