"""A fake store for the dashboard: fixture rows per query, recorded calls (specs/014 T013).

Mirrors `dashboard.store.PostgresStore.read()` and the `Reader`'s methods. Later tasks add
the order, activity and journal queries here as the real store grows them.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal

from trading_agent.dashboard.store import StoreError

DEFAULT_STATE = {
    "trading_paused": False,
    "trading_paused_changed_at": None,
    "daily_loss_halt_active": False,
    "current_trading_date": date(2026, 10, 19),
}
DEFAULT_SNAPSHOT = {
    "taken_at": datetime(2026, 10, 19, 20, 0, tzinfo=UTC),
    "equity": Decimal("100000.00"),
    "cash": Decimal("40000.00"),
    "buying_power": Decimal("80000.00"),
}


class FakeReader:
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    def _record(self, name: str, *args):
        self._store.calls.append((name, args))
        if self._store.error is not None:
            raise self._store.error

    def state(self):
        self._record("state")
        return self._store.state

    def positions(self):
        self._record("positions")
        return list(self._store.positions_rows)

    def latest_snapshot(self):
        self._record("latest_snapshot")
        return self._store.snapshot

    def equity_points(self, since, daily):
        self._record("equity_points", since, daily)
        return list(self._store.points)

    def orders(self, symbol, since, until, limit, offset):
        self._record("orders", symbol, since, until, limit, offset)
        return list(self._store.order_rows[offset : offset + limit])

    def reports(self, agent, symbol, since, until, limit):
        self._record("reports", agent, symbol, since, until, limit)
        return list(self._store.report_rows[:limit])

    def decisions(self, symbol, since, until, limit):
        self._record("decisions", symbol, since, until, limit)
        return list(self._store.decision_rows[:limit])

    def runs(self, agent, since, until, problems, limit):
        self._record("runs", agent, since, until, problems, limit)
        return list(self._store.run_rows[:limit])

    def report_times(self, ids):
        self._record("report_times", tuple(ids))
        return {str(i): t for i, t in self._store.report_times_map.items() if i in ids}

    def journal_page(self, since, limit, offset):
        self._record("journal_page", since, limit, offset)
        return list(self._store.journal_rows[offset : offset + limit])

    def journal_range(self, since):
        self._record("journal_range", since)
        return list(self._store.journal_range_rows)

    def latest_pm_run(self):
        self._record("latest_pm_run")
        return self._store.pm_run

    def ping(self):
        self._record("ping")
        return self._store.ping_ok


class FakeStore:
    def __init__(
        self,
        *,
        state: dict | None = None,
        positions: list[dict] | None = None,
        snapshot: dict | None = DEFAULT_SNAPSHOT,
        points: list[dict] | None = None,
        pm_run: dict | None = None,
        order_rows: list[dict] | None = None,
        report_rows: list[dict] | None = None,
        decision_rows: list[dict] | None = None,
        run_rows: list[dict] | None = None,
        report_times_map: dict | None = None,
        journal_rows: list[dict] | None = None,
        journal_range_rows: list[dict] | None = None,
        error: Exception | None = None,
        ping_ok: bool = True,
    ) -> None:
        self.state = dict(DEFAULT_STATE if state is None else state)
        self.positions_rows = positions or []
        self.snapshot = snapshot
        self.points = points or []
        self.pm_run = pm_run
        self.order_rows = order_rows or []
        self.report_rows = report_rows or []
        self.decision_rows = decision_rows or []
        self.run_rows = run_rows or []
        self.report_times_map = report_times_map or {}
        self.journal_rows = journal_rows or []
        self.journal_range_rows = journal_range_rows or []
        self.error = error
        self.ping_ok = ping_ok
        self.calls: list[tuple[str, tuple]] = []
        self.read_calls = 0

    @contextmanager
    def read(self):
        self.read_calls += 1
        if self.error is not None:
            raise self.error
        yield FakeReader(self)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def failing() -> FakeStore:
    return FakeStore(error=StoreError("database: OperationalError"))
