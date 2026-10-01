# Research: Orchestrator

Decisions behind [plan.md](plan.md), numbered O1–O17 so tasks, code comments and reviews can cite them.

## O1. A pure planner, a launcher port, and a thin service

**Decision**: the same shape as Execution and the reference-data job.
- `trading_agent.orchestrator.planner`: pure. Given `now`, the schedule config, today's run records, the latest report time, the pause flag and which agents are running, it returns the actions due: `Start(agent, reason, slot_at)`, `Skip(agent, reason, slot_at, why)`, `Stop(run_id)` for timeouts. It reads no clock, database or environment.
- `trading_agent.orchestrator.launcher`: the `Launcher` protocol (start, poll, stop). There is one real implementation using subprocesses, and a fake for tests.
- `trading_agent.orchestrator.service`: one tick. It reads state, asks the planner, applies the actions through the launcher, and writes run records.
- `__main__`: the loop.

**Why**: every scheduling rule (FR-009–FR-020) becomes a table test or property test with no processes and no database. SC-003 ("never less than 30 minutes apart … across randomised patterns") is a Hypothesis property of the planner.

## O2. A 30-second tick, not a scheduler library

**Decision**: `python -m trading_agent.orchestrator` ticks every 30 seconds. Each tick recomputes what is due from the recorded runs and the calendar. There are no in-memory timers. Slot times are checked in ET wall-clock time, so daylight-saving changes need no special handling.

**Why**:
- Restart safety (FR-020) comes free, because state lives in the run records and not in timers.
- It uses the same pattern as the three existing loops (ADR 0013), and time judgements come from `trading_agent.risk.calendar`.
- A 30-second tick meets "within one minute" (SC-001, SC-005) with margin.

**Alternatives**: APScheduler, as ADR 0009 describes an "APScheduler-style in-process scheduler". The loop is in-process and scheduler-like, so it honours the intent. A library's in-memory job store would duplicate the run records, and would still need the restart re-derivation. It would also add a dependency. Not chosen.

## O3. Agents run as child processes, in their own process group

**Decision**:
- **Starting**: `subprocess.Popen([sys.executable, "-m", module], env=..., start_new_session=True)`, without waiting on it. The tick polls each running child.
- **Output**: stdout and stderr are inherited, so agent logs go straight to the platform's logs (spec Edge Cases). The orchestrator records only the exit status.
- **Outcome**: exit 0 is `succeeded`, anything else is `failed`, and a launch error (such as a missing module) is `failed` with the error type.
- **Timeouts**: at the timeout, the orchestrator sends SIGTERM to the process group, and SIGKILL 10 seconds later if it is still alive. The run is recorded as `timed_out`. Using the process group means a hung agent's own children are stopped too.

- **Its own session is a trade-off** (fixed after `/speckit-analyze` P1). `start_new_session=True` puts the agent in a new process group, so a timeout kills its whole tree. But it also means the agent does **not** die with the orchestrator. So:
  - **On a normal stop** (SIGTERM from the platform during a redeploy, SIGINT, or the loop ending), a signal handler and a `finally` block stop every running group (SIGTERM, then SIGKILL after the grace period) and record those runs as `interrupted` before exiting (FR-005a).
  - **The process-group id** of each run is recorded in `orchestrator_runs.pgid` as soon as it starts.
  - **After a crash**, where no handler ran, startup reaps the orphans; see O12.
- **Grace period**: 10 s by default, injectable for tests. `stop()` blocks the tick for at most that long, which is acceptable at these cadences.

**Why**: this is the owner's clarification: each agent runs as its own process, with a timeout. Not waiting on the child keeps the tick responsive, so one long run never delays another agent's slot (SC-005). [ADR 0015](../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md) records the process and credential shape.

## O4. Each agent's environment: its own prefix plus a small fixed base

**Decision**:
- **What an agent receives**: exactly the variables listed for it in config (FR-004), plus the fixed base set `PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ` and `PYTHONPATH`. Variables that aren't set are left out.
- **The prefix rule**: every listed name must start with the agent's own prefix (`RESEARCH_`, `OPPORTUNISTIC_IDENTIFIER_` or `PORTFOLIO_MANAGER_`). Anything else is a config error, and the orchestrator refuses to start (FR-008). That makes it impossible to list `ALPACA_*`, `ADMIN_DATABASE_URL`, another component's `*_DATABASE_URL`, or another agent's key.
- **Values**: never read beyond copying, and never logged (FR-008a). The env dict is built from names and handed to `Popen`.

**Why**:
- The prefix rule enforces Constitution III, since each component's credential is distinct, and ADR 0004 by construction rather than by a deny-list that could fall out of date.
- Agents will read `RESEARCH_ANTHROPIC_API_KEY` and so on. That is each agent feature's naming decision, recorded in its entry.

## O5. Run records: `orchestrator_runs` (migration 0010)

**Decision**: one row per run or skipped slot. The schema is in [data-model.md](data-model.md).
- **One key per slot** (fixed after `/speckit-analyze` D1). Every row except event-driven ones carries a `slot_key`: `research_daily`, `research@HH:MM` for the optional intraday runs, `morning_session`, or `oi@HH:MM`. One unique index on `(agent, trading_day, slot_key)` covers every reason. So an on-time morning session and a catch-up morning session can't both exist, and neither can two copies of any slot, even across a restart race (FR-020, FR-023a, SC-006). Event-driven rows have a null `slot_key`.
- **Record before start** (fixed after R1). The row is inserted as `running` **before** the process starts, with `started_at` set to the orchestrator's clock; the insert is what claims the slot. Then the process starts and its `pgid` is written. If the start raises `LaunchFailed`, the row becomes `failed`. If the connection is lost in between, the slot is claimed but nothing runs: a restart treats the row as interrupted, and it counts as a missed run, not a double one. That is the safe direction.
- **Writes**: rows are updated once, to their outcome, plus the `pgid` update just after the start. `ta_orchestrator` gets `SELECT, INSERT` and `UPDATE (pgid, finished_at, outcome, detail)` only. No DELETE: records are kept (FR-024).

## O6. The latest report time: a one-value view

**Decision**: `CREATE VIEW latest_report_time AS SELECT max(generated_at) AS generated_at FROM reports`. It is owned by the migration admin and is not `security_invoker`, the same pattern as 0009's view. The row-level security on `reports` is left unchanged: it is enabled, not forced, and the owner bypasses it. SELECT is granted to `ta_orchestrator`, `ta_assistant` and `ta_dashboard`.

**Why**: this is the owner's clarification: a single value, never report contents (docs/specs/orchestrator.md "Interfaces").

## O7. Narrowing `ta_orchestrator`'s read of `system_state`

**Decision**: migration 0010 revokes `SELECT` on `system_state` and on `system_state_effective` from `ta_orchestrator`, then grants column-level `SELECT (trading_paused)` on `system_state`. That is the same narrowing 0007 gave Execution.

**Why**: migration 0005 gave the orchestrator the whole row, including `daily_starting_equity` and the halt state. That is account data FR-002 forbids, and the orchestrator only needs the pause flag (FR-016). This tightens an existing grant, and no ADR is needed: it makes the database match ADR 0003's "no read access to trading data".

**Also**: `system_state_effective` computes nothing the orchestrator needs, because `trading_paused` is stored as it is.

## O8. Event-driven PM rule and morning-session gating (FR-013, FR-014, FR-018)

**Decision**: these are pure functions of the day's records.
- **Morning slot done**: a PM record exists today with `slot_key = 'morning_session'`, whatever its reason (`morning_session` or `catch_up`) and outcome, including `skipped`.
- **Research daily done**: a Research record exists today with `slot_key = 'research_daily'`, any reason or outcome (fixed after D1).
- **Last PM start**: the latest `started_at` over all PM records with a start (any day), used for the spacing.
- **Last successful PM start**: the latest `started_at` over PM records with outcome `succeeded`.
- **Due**: all of the following hold:
  - the morning slot is done;
  - no PM run is in progress;
  - `latest_report_time` is later than the last successful PM start;
  - `now ≥ latest_report_time + wait` (5 minutes);
  - `now ≥ last PM start + spacing` (30 minutes);
  - `now < cutoff`;
  - trading is not paused.
- **Pre-morning reports** (FR-014): the morning session starts after them, so if it succeeds they are no longer newer than it. If it fails, FR-013's rule retries them (clarification 2).

## O9. The cutoff and the slot lists

**Decision**: `cutoff(day) = min(pm_last_start (15:30 ET), close(day) − 30 min)`, with the close from `calendar.close_time`.
- **Identifier slots**: every `interval` from `window_start`, up to `min(window_end, cutoff)`.
- **Research**: its daily time, plus optional intraday slots when `interval` is set.
- **Missed slots**: a slot counts as missed if `now` is past it and it has no record. Only the current slot (the latest one at or before `now`) may run for the Identifier and for Research's optional intraday runs. Those are never backfilled. Research's daily slot and the morning session run once if missed, as long as `now` is still before the cutoff (clarification 4).
- **On time or catch-up** (fixed after A1): a daily slot started within one tick (30 s) of its time is recorded with its normal reason (`scheduled` or `morning_session`). Started later, it is `catch_up`. The one exception is a morning session held back only because Research is still running (O10). That is still `morning_session`, whatever the delay. The difference is a label for the owner; the one slot key means the rules treat both the same.
- **Order within a tick**: the planner returns starts in a fixed order: Research, then the PM, then the Identifier. A morning session is never started in the same tick as a Research start (O10).

## O10. Order and concurrency

**Decision**:
- **One run per agent**: an agent with a run in progress gets no new start. A due slot that arrives meanwhile is recorded as `skipped` ("previous run in progress"), once per slot (FR-006).
- **Morning session waits for Research**: the morning session is not started while a Research run is in progress (clarification 4, generalised: the PM should see Research's report).
- **Different agents** can run at the same time.

## O11. The pause (FR-016–FR-018)

**Decision**:
- **Checking**: the pause is read in the same tick, immediately before any PM start. If it can't be read, it is treated as paused (fail closed).
- **Morning session while paused**: recorded as `skipped` ("trading paused"), and the slot counts as done.
- **Event-driven run while paused**: no record is written, to avoid a skip row every 30 seconds. It is logged once per pause episode, held in memory.

## O12. Startup and restart

**Decision**, in order:
1. Read the environment (`ORCHESTRATOR_DATABASE_URL` only) and the config, and validate the prefix rule (O4). Any failure exits with code 2.
2. Connect with autocommit and keepalives.
3. Take the single-instance advisory lock `0x6f726368` ("orch"), waiting 5 minutes. This key is distinct from Execution's `0x65786531` and `0x65786563`, the gate's `0x7269736B`, and the reference job's `0x72656631`. If the wait runs out, exit with code 2.
4. **Reap orphans** (fixed after P1). For each `running` row with a `pgid`, check whether that process group is still alive and is still running that agent: its leader's command line contains the agent's module, read from `/proc/<pgid>/cmdline` where available. If so, stop the group (SIGTERM, then SIGKILL after the grace period). Checking the command line guards against stopping an unrelated process that happens to have reused the id. Then mark the row `interrupted`, with `finished_at = now` (FR-023). Rows without a `pgid` (claimed but never started) become `interrupted` too.
5. Install the SIGTERM and SIGINT handlers (O3), then tick.

A lost database connection exits with code 3 (FR-022). The `finally` block still stops every running group first. It can't record them while the connection is down, so the next startup marks them `interrupted` through step 4, and finds their groups already gone.

## O13. Configuration: `config/schedule.yaml`, strict and bounded

**Decision**: one entry per agent: `enabled`, `module`, `env` (list of names), `timeout_minutes`, and the cadence fields. There is also a PM block with `morning_session`, `min_spacing_minutes`, `report_wait_minutes`, `last_start` and `before_close_minutes`. The loader is as strict as `risk.yaml`'s:
- `module` must match `^trading_agent\.[a-z_]+$`;
- times are `HH:MM` in ET;
- `min_spacing_minutes` must be at least 30, `last_start` no later than 15:30, `before_close_minutes` at least 30, and `report_wait_minutes` between 5 and 60 (FR-015: never looser than ADR 0011 or the spec);
- `morning_session` must be at or after 09:30 and before `last_start`, and Research's `daily_at` before 09:30;
- timeouts are between 1 and 120 minutes.

It ships with all three agents `enabled: false` (FR-007).

## O16. Where timestamps come from

**Decision** (fixed after U1): every time the orchestrator stores (`started_at`, `finished_at`, `slot_at`) comes from its injected clock, like every decision it makes. `reports.generated_at` comes from the database's `now()`. Rules (a) and (b) of FR-013 compare the two, so a clock difference between the orchestrator's host and the database shifts the 5-minute wait by that amount. Railway hosts are NTP-synced, so the difference is well under a second, and it is tolerated. The integration tests use explicit report times, never `now()`, because `now()` is frozen inside a test transaction.

## O17. Fixes from the adversarial review (2026-10-01)

- **H1, future-dated reports**: `latest_report_time` counts only `generated_at <= now()`. Analysts can set `generated_at`, so one report dated ahead would otherwise stay "the newest" and block every event-driven run until its date. Only the future is excluded, so fixed past dates in tests can't expire.
- **M1, leftover children**: `SubprocessLauncher.poll` kills the process group once the leader has exited.
- **M2, PM timeout**: the loader requires the PM's `timeout_minutes` to be at most `before_close_minutes`. A run started just before the cutoff is then over by the close (ADR 0011).
- **M3, signals**: the SIGTERM and SIGINT handler only sets a `StopFlag`. The loop checks it between ticks and during the sleep, which is taken a second at a time. A tick in progress always finishes tracking what it started. `_stop` drops a handle only after `stop` returns. A second signal just sets the flag again.
- **L1**: a run being stopped this tick no longer counts as running, so its next slot starts instead of being skipped. The loader requires each interval to be longer than the agent's timeout.
- **L2**: a missing `system_state` row reads as unknown (`None`), which fails closed.
- **L4**: the morning session must be before the early-close cutoff (`13:00 − before_close`).
- **L5**: any other database error is logged as such, the agents are stopped, and the process exits with code 3.
- **L6**: a trigger rejects updates to finished rows.
- **L3, not changed**: run times use the orchestrator's clock and report times the database's. The difference is tolerated as before (O16).

## O14. Tests

**Decision**:
- **Planner**: table tests plus Hypothesis properties (SC-003, SC-004, SC-006: re-planning from the same records starts nothing twice).
- **Service**: tested with a fake launcher and an in-memory record store.
- **Restart property** (fixed after G3): restart at a random time in a random day. Records written before the restart stay, `running` ones become `interrupted`, and planning resumes. The property is: no slot started twice, and the daily Research run and the morning session each happen at most once, and at least once if they were still due before the cutoff.
- **Hung agent** (SC-005): a service test where one agent hangs past its timeout while the others' slots arrive. The others start on time, and the hung one is stopped within one tick of its timeout.
- **Launcher**: the real one is tested with actual child processes running `tests/fakes/agent.py`, a stand-in that exits 0, exits 1, sleeps past its timeout, spawns a child, or prints the names of its environment variables. No model calls and no network (FR-028). The network guard stays as it is.
- **Migration 0010**: integration tests, plus the both-ways grants matrix.
- **Mutation checks**: the planner's rules are mutation-checked, as before.

## O15. What this feature doesn't do

- **Agents**: none built, and none enabled.
- **Deployment**: no deployment config.
- **Watching**: no monitoring of Execution, the gate or the reference job (FR-001).
- **Agent output**: no parsing of what agents write (FR-003).
- **Alerts**: none. Run records and logs are the signal until the Assistant and dashboard exist.
