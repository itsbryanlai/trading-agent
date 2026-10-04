"""A stand-in for the Portfolio Manager's store: fixed inputs, recorded reads and writes,
and the option to fail either (specs/008-portfolio-manager contracts/ports.md)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from trading_agent.portfolio_manager.answer import CheckedDecision
from trading_agent.portfolio_manager.inputs import Inputs
from trading_agent.portfolio_manager.store import StoreError


class FakePmStore:
    def __init__(
        self,
        inputs: Inputs,
        *,
        fail_read: bool = False,
        fail_write: bool = False,
    ) -> None:
        self.inputs = inputs
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.reads: list[tuple[datetime, int]] = []
        self.writes: list[tuple[CheckedDecision, ...]] = []

    def read_inputs(self, run_start: datetime, *, journal_entries: int) -> Inputs:
        self.reads.append((run_start, journal_entries))
        if self.fail_read:
            raise StoreError("OperationalError")
        return self.inputs

    def write(self, decisions: Sequence[CheckedDecision]) -> None:
        if self.fail_write:
            raise StoreError("OperationalError")
        self.writes.append(tuple(decisions))

    @property
    def written(self) -> tuple[CheckedDecision, ...]:
        """Every decision written, across all writes."""
        return tuple(d for batch in self.writes for d in batch)
