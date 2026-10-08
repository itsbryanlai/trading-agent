"""The journal writer's database access (data-model.md, research J4, J7, J11).

Reads run as `ta_journal` in one `REPEATABLE READ, READ ONLY` transaction, so the previous
row, the report window and the day's facts come from a single snapshot. The columns are
listed by name: nothing here selects text a model or the broker wrote. The write is one
upsert in its own transaction, so a re-run replaces the day's row.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from trading_agent.journal.model import JournalReads, JournalRow

_NEW_YORK = ZoneInfo("America/New_York")

_PREVIOUS = (
    "SELECT trading_day, equity_close, per_agent_attribution FROM journal "
    "WHERE trading_day < %s ORDER BY trading_day DESC LIMIT 1"
)
_FUTURE = "SELECT EXISTS (SELECT 1 FROM journal WHERE trading_day > %s) AS found"
_REPORTS = (
    "SELECT id, agent, generated_at, symbol, direction, suggested_size_pct FROM reports "
    "WHERE generated_at > %s AND generated_at <= %s ORDER BY generated_at, id"
)
_DECISION_REPORTS = (
    "SELECT decision_id, report_id FROM decision_reports WHERE report_id = ANY(%s::uuid[])"
)
_DECISIONS = (
    "SELECT id, generated_at, symbol, direction FROM decisions "
    "WHERE id = ANY(%s::uuid[]) OR (generated_at >= %s AND generated_at < %s) "
    "ORDER BY generated_at, id"
)
_VERDICTS = (
    "SELECT id, decision_id, stop_loss_trigger_id, trading_day, verdict, rejection_rule "
    "FROM risk_verdicts WHERE decision_id = ANY(%s::uuid[]) OR trading_day = %s ORDER BY id"
)
_ORDERS = (
    "SELECT risk_verdict_id, status, fill_qty FROM orders "
    "WHERE risk_verdict_id = ANY(%s::uuid[]) ORDER BY id"
)
_REFUSALS = (
    "SELECT reason, refused_at FROM execution_refusals "
    "WHERE refused_at >= %s AND refused_at < %s ORDER BY refused_at"
)
_TRIGGERS = (
    "SELECT id, observed_at FROM stop_loss_triggers "
    "WHERE observed_at >= %s AND observed_at < %s ORDER BY observed_at"
)
_SNAPSHOT_OPEN = (
    "SELECT taken_at, equity FROM account_snapshots "
    "WHERE taken_at >= %(start)s AND taken_at < %(end)s "
    "ORDER BY (taken_at <= %(open)s) DESC, "
    "CASE WHEN taken_at <= %(open)s THEN taken_at END DESC, taken_at ASC LIMIT 1"
)
_SNAPSHOT_CLOSE = (
    "SELECT taken_at, equity FROM account_snapshots "
    "WHERE taken_at >= %s AND taken_at < %s ORDER BY taken_at DESC LIMIT 1"
)
_UPSERT = (
    "INSERT INTO journal (trading_day, equity_open, equity_close, summary_md, "
    "per_agent_attribution) VALUES (%s, %s, %s, %s, %s) "
    "ON CONFLICT (trading_day) DO UPDATE SET equity_open = EXCLUDED.equity_open, "
    "equity_close = EXCLUDED.equity_close, summary_md = EXCLUDED.summary_md, "
    "per_agent_attribution = EXCLUDED.per_agent_attribution, written_at = now()"
)


class PgJournalStore:
    """Needs a connection with the `dict_row` row factory (as `storage.db.connect` opens)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def read(
        self,
        day: date,
        open_at: datetime,
        close_at: datetime,
        previous_close_of: Callable[[date], datetime],
    ) -> JournalReads:
        """Everything the run needs, from one snapshot (research J4, J7, J11).

        On an idle connection the snapshot is a `REPEATABLE READ, READ ONLY` transaction. A
        caller that already holds a transaction (the tests) gets reads inside it.
        """
        conn = self._conn
        if conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
        try:
            with conn.transaction():
                return self._read(day, open_at, close_at, previous_close_of)
        finally:
            if conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
                conn.isolation_level = None
                conn.read_only = None

    def _read(self, day, open_at, close_at, previous_close_of) -> JournalReads:
        conn = self._conn
        start, end = _new_york_day(day)
        previous = conn.execute(_PREVIOUS, (day,)).fetchone()
        future = conn.execute(_FUTURE, (day,)).fetchone()["found"]
        if previous is None:
            # First-ever run: today's reports only (spec, clarify Q2).
            window_start = start - timedelta(microseconds=1)
        else:
            window_start = previous_close_of(previous["trading_day"])
        reports = conn.execute(_REPORTS, (window_start, close_at)).fetchall()
        report_ids = [r["id"] for r in reports]
        links = conn.execute(_DECISION_REPORTS, (report_ids,)).fetchall()
        decisions = conn.execute(
            _DECISIONS, ([link["decision_id"] for link in links], start, end)
        ).fetchall()
        verdicts = conn.execute(_VERDICTS, ([d["id"] for d in decisions], day)).fetchall()
        orders = conn.execute(_ORDERS, ([v["id"] for v in verdicts],)).fetchall()
        return JournalReads(
            previous=previous,
            has_future_row=future,
            window_start=None if previous is None else window_start,
            reports=reports,
            decision_reports=links,
            decisions=decisions,
            verdicts=verdicts,
            orders=orders,
            refusals=conn.execute(_REFUSALS, (start, end)).fetchall(),
            triggers=conn.execute(_TRIGGERS, (start, end)).fetchall(),
            snapshot_open=conn.execute(
                _SNAPSHOT_OPEN, {"start": start, "end": end, "open": open_at}
            ).fetchone(),
            snapshot_close=conn.execute(_SNAPSHOT_CLOSE, (start, end)).fetchone(),
        )

    def upsert(self, row: JournalRow) -> None:
        """Write the day's row, replacing any earlier one for the same day (research J11)."""
        with self._conn.transaction():
            self._conn.execute(
                _UPSERT,
                (
                    row.trading_day,
                    row.equity_open,
                    row.equity_close,
                    row.summary_md,
                    Jsonb(row.per_agent_attribution),
                ),
            )


def _new_york_day(day: date) -> tuple[datetime, datetime]:
    """The half-open span of the New York calendar date, as aware datetimes."""
    start = datetime(day.year, day.month, day.day, tzinfo=_NEW_YORK)
    following = day + timedelta(days=1)
    return start, datetime(following.year, following.month, following.day, tzinfo=_NEW_YORK)
