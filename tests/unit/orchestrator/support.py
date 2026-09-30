"""Shared helpers for the orchestrator's unit tests: configs, ET times, states,
an in-memory RunStore, and a simulator that feeds the planner's own actions back
as records."""

from __future__ import annotations

import dataclasses
import itertools
from datetime import UTC, date, datetime, time, timedelta

from trading_agent.orchestrator import planner as p
from trading_agent.orchestrator.config import DEFAULT_CONFIG_PATH, load_config
from trading_agent.orchestrator.planner import RunRecord, State

MONDAY = date(2026, 9, 28)
SATURDAY = date(2026, 9, 26)
THANKSGIVING = date(2026, 11, 26)
EARLY_CLOSE = date(2026, 11, 27)
WINTER = date(2026, 12, 7)


def et(hhmm: str, day: date = MONDAY, seconds: int = 0) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return p.at(day, time(h, m, seconds)).astimezone(UTC)


def config(enabled=("research", "opportunistic_identifier", "portfolio_manager"), **changes):
    """The shipped schedule with the named agents enabled; `changes` override fields
    as {agent: {field: value}}."""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    agents = {}
    for agent in cfg.agents():
        fields = {"enabled": agent.name in enabled}
        fields.update(changes.get(agent.name, {}))
        agents[agent.name] = dataclasses.replace(agent, **fields)
    return dataclasses.replace(
        cfg,
        research=agents["research"],
        identifier=agents["opportunistic_identifier"],
        portfolio_manager=agents["portfolio_manager"],
    )


_ids = itertools.count(1)


def record(
    agent,
    *,
    reason="scheduled",
    slot_key=None,
    slot_at=None,
    started=None,
    finished=None,
    outcome=None,
    day=MONDAY,
):
    if outcome is None:
        outcome = "running" if finished is None and started is not None else "succeeded"
    return RunRecord(
        id=next(_ids),
        agent=agent,
        trading_day=day,
        reason=reason,
        slot_key=slot_key,
        slot_at=slot_at,
        started_at=started,
        finished_at=finished,
        outcome=outcome,
    )


def state(records=(), *, latest_report=None, paused=False, day=MONDAY) -> State:
    records = tuple(records)
    pm = [r for r in records if r.agent == "portfolio_manager" and r.started_at is not None]
    ok = [r for r in pm if r.outcome == "succeeded"]
    return State(
        today=tuple(r for r in records if r.trading_day == day),
        running=tuple(r for r in records if r.outcome == "running"),
        last_pm_start=max((r.started_at for r in pm), default=None),
        last_successful_pm_start=max((r.started_at for r in ok), default=None),
        latest_report=latest_report,
        paused=paused,
    )


class Simulator:
    """Ticks the planner and turns its actions into records, as the service would.
    Agents finish `run_for` after starting (or never, if `run_for` is None)."""

    def __init__(
        self,
        cfg,
        *,
        run_for=timedelta(minutes=2),
        paused=lambda now: False,
        outcome=lambda action, now: "succeeded",
    ):
        self.cfg = cfg
        self.run_for = run_for
        self.paused = paused
        self.outcome = outcome
        self.records: list[RunRecord] = []
        self.reports: list[datetime] = []
        self.holds: list[tuple[datetime, str]] = []
        self._outcomes: dict = {}

    def _finish_due(self, now):
        for i, r in enumerate(self.records):
            if r.outcome == "running" and self.run_for is not None:
                if now >= r.started_at + self.run_for:
                    self.records[i] = dataclasses.replace(
                        r,
                        outcome=self._outcomes.get(r.id, "succeeded"),
                        finished_at=r.started_at + self.run_for,
                    )

    def state(self, now):
        latest = max((t for t in self.reports if t <= now), default=None)
        return state(
            self.records,
            latest_report=latest,
            paused=self.paused(now),
            day=p.calendar.trading_day(now),
        )

    def tick(self, now):
        self._finish_due(now)
        actions = p.plan(now, self.cfg, self.state(now))
        for action in actions:
            if isinstance(action, p.Start):
                rid = next(_ids)
                self._outcomes[rid] = self.outcome(action, now)
                self.records.append(
                    RunRecord(
                        id=rid,
                        agent=action.agent,
                        trading_day=p.calendar.trading_day(now),
                        reason=action.reason,
                        slot_key=action.slot_key,
                        slot_at=action.slot_at,
                        started_at=now,
                        finished_at=None,
                        outcome="running",
                    )
                )
            elif isinstance(action, p.Skip):
                self.records.append(
                    RunRecord(
                        id=next(_ids),
                        agent=action.agent,
                        trading_day=p.calendar.trading_day(now),
                        reason=action.reason,
                        slot_key=action.slot_key,
                        slot_at=action.slot_at,
                        started_at=None,
                        finished_at=None,
                        outcome="skipped",
                    )
                )
            elif isinstance(action, p.Stop):
                for i, r in enumerate(self.records):
                    if r.id == action.run_id:
                        self.records[i] = dataclasses.replace(
                            r, outcome="timed_out", finished_at=now
                        )
            elif isinstance(action, p.Hold):
                self.holds.append((now, action.why))
        return actions

    def run(self, start, end, step=timedelta(seconds=30)):
        now = start
        while now <= end:
            self.tick(now)
            now += step
        return self

    def starts(self, agent=None):
        return [
            r
            for r in self.records
            if r.started_at is not None and (agent is None or r.agent == agent)
        ]


class MemoryStore:
    """The RunStore in memory, with the unique-slot rule the database enforces."""

    def __init__(self):
        self.records: dict[object, RunRecord] = {}
        self.latest_report: datetime | None = None
        self.paused: bool = False
        self.read_error: dict[str, Exception] = {}
        self.calls: list[str] = []

    def _raise(self, what):
        error = self.read_error.get(what)
        if error is not None:
            raise error

    def today_runs(self, day):
        return [r for r in self.records.values() if r.trading_day == day]

    def running_rows(self):
        return [r for r in self.records.values() if r.outcome == "running"]

    def last_pm_start(self):
        return max(
            (
                r.started_at
                for r in self.records.values()
                if r.agent == "portfolio_manager" and r.started_at
            ),
            default=None,
        )

    def last_successful_pm_start(self):
        return max(
            (
                r.started_at
                for r in self.records.values()
                if r.agent == "portfolio_manager" and r.outcome == "succeeded"
            ),
            default=None,
        )

    def _claim(self, agent, day, slot_key):
        from trading_agent.orchestrator.service import SlotTaken

        if slot_key is not None and any(
            (r.agent, r.trading_day, r.slot_key) == (agent, day, slot_key)
            for r in self.records.values()
        ):
            raise SlotTaken(slot_key)

    def insert_start(self, start, day, now):
        self.calls.append(f"insert_start:{start.agent}")
        self._claim(start.agent, day, start.slot_key)
        rid = next(_ids)
        self.records[rid] = RunRecord(
            rid, start.agent, day, start.reason, start.slot_key, start.slot_at, now, None, "running"
        )
        return rid

    def set_pgid(self, run_id, pgid):
        self.records[run_id] = dataclasses.replace(self.records[run_id], pgid=pgid)

    def insert_skip(self, skip, day):
        self._claim(skip.agent, day, skip.slot_key)
        rid = next(_ids)
        self.records[rid] = RunRecord(
            rid, skip.agent, day, skip.reason, skip.slot_key, skip.slot_at, None, None, "skipped"
        )

    def finish(self, run_id, outcome, detail, now):
        r = self.records[run_id]
        if r.outcome == "running":
            self.records[run_id] = dataclasses.replace(r, outcome=outcome, finished_at=now)
            self.details = {**getattr(self, "details", {}), run_id: detail}

    def latest_report_time(self):
        self._raise("latest")
        return self.latest_report

    def trading_paused(self):
        self._raise("paused")
        return self.paused

    def by_agent(self, agent):
        return sorted(
            (r for r in self.records.values() if r.agent == agent),
            key=lambda r: r.started_at or r.slot_at,
        )
