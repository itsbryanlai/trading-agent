"""An in-memory Opportunistic Identifier store: the open symbols it is told, and the rows it
is asked to write, in order. Either call can be made to fail."""

from __future__ import annotations

from datetime import datetime

from trading_agent.opportunistic_identifier.answer import ReportRow


class FakeOIStore:
    def __init__(self, open_symbols=(), *, fail_read=None, fail_write=None) -> None:
        self.open = frozenset(open_symbols)
        self.fail_read, self.fail_write = fail_read, fail_write
        self.reads: list[datetime] = []
        self.writes: list[list[ReportRow]] = []

    def open_symbols(self, now: datetime) -> frozenset[str]:
        self.reads.append(now)
        if self.fail_read is not None:
            raise self.fail_read
        return self.open

    def write(self, rows: list[ReportRow]) -> None:
        if self.fail_write is not None:
            raise self.fail_write
        self.writes.append(list(rows))

    @property
    def rows(self) -> list[ReportRow]:
        return [row for batch in self.writes for row in batch]
