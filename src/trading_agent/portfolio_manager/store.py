"""The PM's database port (specs/008-portfolio-manager research P6, P10; contracts/ports.md).

`PostgresStore` connects as `ta_portfolio_manager`, whose grants are exactly: SELECT on
reports (through `reports_with_status`), positions, account_snapshots, journal and
decisions, and INSERT on decisions and decision_reports. It reads no verdict, order or
risk setting, and writes nothing else.

- `read_inputs`: one `REPEATABLE READ, READ ONLY` transaction, so the state the model
  sees is one consistent snapshot.
- `write`: one transaction for every decision and its report links, all or nothing.

Any database failure becomes `StoreError` (exit 3 in `__main__`); the message names the
error type and never its text.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

import psycopg
from psycopg.rows import dict_row

from trading_agent.portfolio_manager.answer import CheckedDecision
from trading_agent.portfolio_manager.inputs import (
    AccountRecord,
    DecisionRecord,
    Inputs,
    JournalRecord,
    PositionRecord,
    ReportRecord,
)
from trading_agent.risk import calendar

__all__ = ["Inputs", "PostgresStore", "Store", "StoreError"]

BEGIN_READ = "BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY"


class StoreError(Exception):
    """A read or the write failed, or the database could not be reached."""


class Store(Protocol):
    def read_inputs(self, run_start: datetime, *, journal_entries: int) -> Inputs: ...

    def write(self, decisions: Sequence[CheckedDecision]) -> None: ...


class PostgresStore:
    """`conn` must be autocommit, so the read can open its own repeatable-read
    transaction and the write can be a real one. `_allow_savepoints` is for tests that
    run inside an outer transaction they roll back: the read then runs in a savepoint,
    at that transaction's isolation level."""

    def __init__(self, conn: psycopg.Connection, *, _allow_savepoints: bool = False) -> None:
        if not conn.autocommit and not _allow_savepoints:
            raise StoreError("NotAutocommit")
        self.conn = conn
        self._savepoints = not conn.autocommit

    def read_inputs(self, run_start: datetime, *, journal_entries: int) -> Inputs:
        try:
            if self._savepoints:
                with self.conn.transaction():
                    return self._read(run_start, journal_entries)
            self.conn.execute(BEGIN_READ)
            try:
                return self._read(run_start, journal_entries)
            finally:
                self.conn.execute("ROLLBACK")  # nothing was written: ends the snapshot
        except psycopg.Error as exc:
            raise StoreError(type(exc).__name__) from None

    def write(self, decisions: Sequence[CheckedDecision]) -> None:
        try:
            with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
                for d in decisions:
                    row = cur.execute(
                        "INSERT INTO decisions (symbol, direction, size_pct, reasoning_md,"
                        " quote_at_decision, quote_time) VALUES (%s, %s, %s, %s, %s, %s)"
                        " RETURNING id",
                        (d.symbol, d.direction, d.size_pct, d.reasoning, d.quote, d.quote_time),
                    ).fetchone()
                    for report_id in d.report_ids:
                        cur.execute(
                            "INSERT INTO decision_reports (decision_id, report_id) VALUES (%s, %s)",
                            (row["id"], report_id),
                        )
        except psycopg.Error as exc:
            raise StoreError(type(exc).__name__) from None

    # --- the reads ------------------------------------------------------------------

    def _read(self, run_start: datetime, journal_entries: int) -> Inputs:
        today = calendar.trading_day(run_start)
        with self.conn.cursor(row_factory=dict_row) as cur:
            reports = tuple(
                ReportRecord(
                    id=str(r["id"]),
                    agent=r["agent"],
                    symbol=r["symbol"],
                    direction=r["direction"],
                    conviction=r["conviction"],
                    suggested_size_pct=r["suggested_size_pct"],
                    sources=tuple(r["sources"]),
                    rationale_md=r["rationale_md"],
                    generated_at=r["generated_at"],
                    expires_at=r["expires_at"],
                    consumed=r["status"] == "consumed",
                )
                for r in cur.execute(
                    "SELECT id, agent, symbol, direction, conviction, suggested_size_pct,"
                    " sources, rationale_md, generated_at, expires_at, status"
                    " FROM reports_with_status"
                    " WHERE expires_at > %s AND direction <> 'no_action'"
                    " ORDER BY generated_at, id",
                    (run_start,),
                )
            )
            positions = tuple(
                PositionRecord(r["symbol"], r["qty"], r["avg_entry_price"])
                for r in cur.execute(
                    "SELECT symbol, qty, avg_entry_price FROM positions ORDER BY symbol"
                )
            )
            snapshot = cur.execute(
                "SELECT equity, cash, taken_at FROM account_snapshots"
                " WHERE (taken_at AT TIME ZONE 'America/New_York')::date = %s AND taken_at <= %s"
                " ORDER BY taken_at DESC LIMIT 1",
                (today, run_start),
            ).fetchone()
            journal = tuple(
                reversed(  # oldest first
                    [
                        JournalRecord(
                            r["trading_day"], r["equity_open"], r["equity_close"], r["summary_md"]
                        )
                        for r in cur.execute(
                            "SELECT trading_day, equity_open, equity_close, summary_md"
                            " FROM journal ORDER BY trading_day DESC LIMIT %s",
                            (journal_entries,),
                        )
                    ]
                )
            )
            symbols = sorted({r.symbol for r in reports if r.symbol})
            earlier = tuple(
                DecisionRecord(r["symbol"], r["direction"], r["size_pct"], r["generated_at"])
                for r in cur.execute(
                    "SELECT symbol, direction, size_pct, generated_at FROM decisions"
                    " WHERE symbol = ANY(%s)"
                    " AND (generated_at AT TIME ZONE 'America/New_York')::date = %s"
                    " ORDER BY generated_at, id",
                    (symbols, today),
                )
            )
        return Inputs(
            reports=reports,
            positions=positions,
            account=None
            if snapshot is None
            else AccountRecord(snapshot["equity"], snapshot["cash"], snapshot["taken_at"]),
            journal=journal,
            earlier_decisions=earlier,
        )
