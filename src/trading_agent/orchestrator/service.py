"""One tick of the orchestrator, plus startup and shutdown (research O1, O3, O5, O12).

Each tick: record agents that have finished, read the state (today's records,
the latest report time, the pause flag), ask the planner, and apply its actions.
A run is recorded before its process starts, so the record claims the slot. Each
agent gets only its listed variables plus a fixed non-secret base (ADR 0015),
and their values are never read beyond copying, logged or recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Protocol

import psycopg
from psycopg.rows import dict_row, tuple_row

from trading_agent.orchestrator import planner as p
from trading_agent.orchestrator.config import ScheduleConfig
from trading_agent.orchestrator.launcher import Handle, Launcher, LaunchFailed
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.orchestrator")

# Passed to every agent: nothing secret, just what a Python process needs (O4).
BASE_ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "PYTHONPATH")


class NotAutocommit(Exception):
    """Each record must be its own transaction."""


class SlotTaken(Exception):
    """Another record already claims this slot today (the unique index said so)."""


class RunStore(Protocol):
    def today_runs(self, day: date) -> list[p.RunRecord]: ...

    def running_rows(self) -> list[p.RunRecord]: ...

    def last_pm_start(self) -> datetime | None: ...

    def last_successful_pm_start(self) -> datetime | None: ...

    def insert_start(self, start: p.Start, day: date, now: datetime) -> object: ...

    def set_pgid(self, run_id: object, pgid: int) -> None: ...

    def insert_skip(self, skip: p.Skip, day: date) -> None: ...

    def finish(self, run_id: object, outcome: str, detail: str | None, now: datetime) -> None: ...

    def latest_report_time(self) -> datetime | None: ...

    def trading_paused(self) -> bool | None: ...


_COLUMNS = (
    "id, agent, trading_day, reason, slot_key, slot_at, started_at, finished_at, outcome, pgid"
)


class PgRunStore:
    """The orchestrator's records, as ta_orchestrator (migration 0010)."""

    def __init__(self, conn: psycopg.Connection, *, _allow_savepoints: bool = False) -> None:
        if not conn.autocommit and not _allow_savepoints:
            raise NotAutocommit("the orchestrator needs an autocommit connection")
        self.conn = conn

    def _rows(self, sql: str, params=()) -> list[p.RunRecord]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [p.RunRecord(**row) for row in cur.fetchall()]

    def _one(self, sql: str, params=()):
        with self.conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None

    def today_runs(self, day: date) -> list[p.RunRecord]:
        return self._rows(
            f"SELECT {_COLUMNS} FROM orchestrator_runs WHERE trading_day = %s", (day,)
        )

    def running_rows(self) -> list[p.RunRecord]:
        return self._rows(f"SELECT {_COLUMNS} FROM orchestrator_runs WHERE outcome = 'running'")

    def last_pm_start(self) -> datetime | None:
        return self._one(
            "SELECT max(started_at) FROM orchestrator_runs WHERE agent = 'portfolio_manager'"
        )

    def last_successful_pm_start(self) -> datetime | None:
        return self._one(
            "SELECT max(started_at) FROM orchestrator_runs "
            "WHERE agent = 'portfolio_manager' AND outcome = 'succeeded'"
        )

    def insert_start(self, start: p.Start, day: date, now: datetime) -> object:
        try:
            with self.conn.transaction(), self.conn.cursor(row_factory=tuple_row) as cur:
                cur.execute(
                    "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, "
                    "slot_at, started_at, outcome) VALUES (%s, %s, %s, %s, %s, %s, 'running') "
                    "RETURNING id",
                    (start.agent, day, start.reason, start.slot_key, start.slot_at, now),
                )
                return cur.fetchone()[0]
        except psycopg.errors.UniqueViolation:
            raise SlotTaken(start.slot_key) from None

    def set_pgid(self, run_id: object, pgid: int) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE orchestrator_runs SET pgid = %s WHERE id = %s", (pgid, run_id)
            )

    def insert_skip(self, skip: p.Skip, day: date) -> None:
        try:
            with self.conn.transaction():
                self.conn.execute(
                    "INSERT INTO orchestrator_runs (agent, trading_day, reason, slot_key, "
                    "slot_at, outcome, detail) VALUES (%s, %s, %s, %s, %s, 'skipped', %s)",
                    (skip.agent, day, skip.reason, skip.slot_key, skip.slot_at, skip.why),
                )
        except psycopg.errors.UniqueViolation:
            raise SlotTaken(skip.slot_key) from None

    def finish(self, run_id: object, outcome: str, detail: str | None, now: datetime) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE orchestrator_runs SET outcome = %s, detail = %s, finished_at = %s "
                "WHERE id = %s AND outcome = 'running'",
                (outcome, detail, now, run_id),
            )

    def latest_report_time(self) -> datetime | None:
        return self._one("SELECT generated_at FROM latest_report_time")

    def trading_paused(self) -> bool | None:
        # No row means unknown, never "not paused": fail closed (review L2).
        value = self._one("SELECT trading_paused FROM system_state")
        return None if value is None else bool(value)


class Orchestrator:
    def __init__(
        self,
        cfg: ScheduleConfig,
        store: RunStore,
        launcher: Launcher,
        environ_get: Callable[[str], str | None],
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.launcher = launcher
        self._environ_get = environ_get
        self._handles: dict[object, tuple[str, Handle]] = {}
        self._held: set[tuple[str, str]] = set()

    # --- startup and shutdown -----------------------------------------------------

    def startup(self, now: datetime) -> None:
        """Stop agents orphaned by a crash, then record them as interrupted (O12)."""
        for row in self.store.running_rows():
            state = "no process group"
            if row.pgid is not None:
                module = self.cfg.agent(row.agent).module
                stopped = self.launcher.stop_group(row.pgid, module)
                state = "process group stopped" if stopped else "process group already gone"
            self.store.finish(row.id, p.INTERRUPTED, "orchestrator restarted", now)
            log.warning(
                "orchestrator: %s run from %s interrupted (%s)",
                row.agent,
                row.started_at.isoformat() if row.started_at else "never started",
                state,
            )

    def shutdown(self, now: datetime) -> None:
        """Stop every running agent before exiting (FR-005a)."""
        if self._handles:
            log.info("orchestrator: stopping %d running agent(s)", len(self._handles))
        for run_id, (agent, handle) in list(self._handles.items()):
            status = self.launcher.stop(handle)
            del self._handles[run_id]
            try:
                self.store.finish(
                    run_id, p.INTERRUPTED, f"orchestrator stopped (exit {status})", now
                )
            except psycopg.Error:
                # The connection may be the reason we're stopping. The next startup
                # records it, and finds the group already gone.
                pass
            log.info("orchestrator: %s stopped at shutdown", agent)

    # --- the tick -----------------------------------------------------------------

    def tick(self, now: datetime) -> list:
        self._collect_finished(now)
        day = calendar.trading_day(now)
        actions = p.plan(now, self.cfg, self._state(day))
        holding = set()
        for action in actions:
            if isinstance(action, p.Stop):
                self._stop(action, now)
            elif isinstance(action, p.Start):
                self._start(action, day, now)
            elif isinstance(action, p.Skip):
                self._skip(action, day)
            elif isinstance(action, p.Hold):
                holding.add((action.agent, action.why))
                if (action.agent, action.why) not in self._held:
                    log.warning("orchestrator: %s due but %s", action.agent, action.why)
        self._held = holding
        return actions

    def _collect_finished(self, now: datetime) -> None:
        for run_id, (agent, handle) in list(self._handles.items()):
            status = self.launcher.poll(handle)
            if status is None:
                continue
            del self._handles[run_id]
            outcome = p.SUCCEEDED if status == 0 else p.FAILED
            detail = f"exit {status}"
            self.store.finish(run_id, outcome, detail, now)
            if outcome == p.SUCCEEDED:
                log.info("orchestrator: %s finished: %s (%s)", agent, outcome, detail)
            else:
                log.warning("orchestrator: %s finished: %s (%s)", agent, outcome, detail)

    def _state(self, day: date) -> p.State:
        latest = self._read("the latest report time", self.store.latest_report_time)
        paused = self._read("the pause flag", self.store.trading_paused)
        return p.State(
            today=tuple(self.store.today_runs(day)),
            running=tuple(self.store.running_rows()),
            last_pm_start=self.store.last_pm_start(),
            last_successful_pm_start=self.store.last_successful_pm_start(),
            latest_report=latest,
            paused=paused,
        )

    def _read(self, what: str, read: Callable):
        """Read failures other than a lost connection mean "unknown" (fail closed)."""
        try:
            return read()
        except psycopg.OperationalError:
            raise
        except psycopg.Error as exc:
            log.error("orchestrator: cannot read %s: %s", what, type(exc).__name__)
            return None

    def _environment(self, agent: str) -> dict[str, str]:
        env = {}
        for name in (*BASE_ENVIRONMENT, *self.cfg.agent(agent).env):
            value = self._environ_get(name)
            if value is not None:
                env[name] = value
        return env

    def _start(self, action: p.Start, day: date, now: datetime) -> None:
        agent = self.cfg.agent(action.agent)
        try:
            run_id = self.store.insert_start(action, day, now)
        except SlotTaken:
            log.warning("orchestrator: %s %s already claimed", action.agent, action.slot_key)
            return
        try:
            handle = self.launcher.start(agent.module, self._environment(action.agent))
        except LaunchFailed as exc:
            self.store.finish(run_id, p.FAILED, f"launch failed: {exc.error_type}", now)
            log.warning(
                "orchestrator: %s %s failed: launch failed: %s",
                action.agent,
                action.reason,
                exc.error_type,
            )
            return
        self._handles[run_id] = (action.agent, handle)
        self.store.set_pgid(run_id, handle.pgid)
        log.info("orchestrator: %s %s started", action.agent, action.reason)

    def _skip(self, action: p.Skip, day: date) -> None:
        try:
            self.store.insert_skip(action, day)
        except SlotTaken:
            return
        log.warning("orchestrator: %s %s skipped: %s", action.agent, action.reason, action.why)

    def _stop(self, action: p.Stop, now: datetime) -> None:
        entry = self._handles.get(action.run_id)
        status = self.launcher.stop(entry[1]) if entry else None
        # Dropped only once it has really stopped, so an interrupted stop is
        # retried at shutdown rather than forgotten (review M3).
        self._handles.pop(action.run_id, None)
        detail = f"exit {status}" if status is not None else "no process to stop"
        self.store.finish(action.run_id, p.TIMED_OUT, detail, now)
        log.warning("orchestrator: %s timed_out: %s", action.agent, detail)
