"""An in-memory stand-in for the journal's store (specs/012 T009).

`read` hands back the `JournalReads` it was given and records its arguments; `insert` and
`replace` record the rows written in `upserts` (`replaces` lists the replaced ones). The J4
window and the equity picks are the real store's job (tests/integration/journal/test_store.py);
the service tests script their results here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Any

from trading_agent.journal.model import JournalReads, JournalRow


def empty_reads(**over: Any) -> JournalReads:
    """A first-ever run with no reports and no snapshots; override what a test needs."""
    base = JournalReads(
        previous=None,
        has_future_row=False,
        window_start=None,
        reports=[],
        decision_reports=[],
        decisions=[],
        verdicts=[],
        orders=[],
        refusals=[],
        triggers=[],
        snapshot_open=None,
        snapshot_close=None,
    )
    return replace(base, **over)


class FakeJournalStore:
    def __init__(
        self, reads: JournalReads | None = None, insert_result: bool | None = None
    ) -> None:
        self.reads = reads or empty_reads()
        # None: the insert succeeds unless the reads say a row exists; False simulates a race.
        self.insert_result = insert_result
        self.read_calls: list[tuple[date, datetime, datetime, Any]] = []
        self.upserts: list[JournalRow] = []
        self.replaces: list[JournalRow] = []

    def read(self, day, open_at, close_at, previous_close_of) -> JournalReads:
        self.read_calls.append((day, open_at, close_at, previous_close_of))
        return self.reads

    def insert(self, row: JournalRow) -> bool:
        inserted = (not self.reads.row_exists) if self.insert_result is None else self.insert_result
        if inserted:
            self.upserts.append(row)
        return inserted

    def replace(self, row: JournalRow) -> None:
        self.upserts.append(row)
        self.replaces.append(row)
