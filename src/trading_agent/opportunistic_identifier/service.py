"""One Opportunistic Identifier run and its database access (specs/011-opportunistic-
identifier research O8, O12).

The store is the agent's only database access: it reads its own still-open reports and
inserts its own `reports` rows, as `ta_opportunistic_identifier`. A run's rows are written
in one transaction (FR-015).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class ReportRow:
    symbol: str | None
    direction: str
    conviction: int | None
    suggested_size_pct: Decimal | None
    sources: list[dict]
    rationale_md: str
    expires_at: datetime


class OIStore(Protocol):
    def open_symbols(self, now: datetime) -> frozenset[str]: ...

    def write(self, rows: list[ReportRow]) -> None: ...


class NotAutocommit(Exception):
    pass


class PgOIStore:
    """The agent's database, as ta_opportunistic_identifier."""

    def __init__(self, conn: psycopg.Connection, *, _allow_savepoints: bool = False) -> None:
        if not conn.autocommit and not _allow_savepoints:
            # Each statement must commit on its own, and the write must be a real
            # transaction rather than a savepoint inside an implicit one.
            raise NotAutocommit("the Opportunistic Identifier needs an autocommit connection")
        self.conn = conn

    def open_symbols(self, now: datetime) -> frozenset[str]:
        """Symbols with a still-open buy report of this agent's own (research O6)."""
        rows = self.conn.execute(
            "SELECT symbol FROM reports WHERE agent = 'opportunistic_identifier' "
            "AND direction <> 'no_action' AND expires_at > %s",
            (now,),
        ).fetchall()
        return frozenset(row["symbol"] if isinstance(row, dict) else row[0] for row in rows)

    def write(self, rows: list[ReportRow]) -> None:
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct,"
                " sources, rationale_md, expires_at)"
                " VALUES ('opportunistic_identifier', %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        r.symbol,
                        r.direction,
                        r.conviction,
                        r.suggested_size_pct,
                        Jsonb(r.sources),
                        r.rationale_md,
                        r.expires_at,
                    )
                    for r in rows
                ],
            )
