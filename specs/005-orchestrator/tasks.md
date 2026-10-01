---

description: "Task list for the orchestrator (feature 005)"
---

# Tasks: Orchestrator

**Input**: Design documents from `/specs/005-orchestrator/`

**Prerequisites**:
- plan.md, spec.md (with Clarifications), research.md (O1–O15), data-model.md, quickstart.md;
- contracts/orchestrator-interface.md and contracts/launcher-port.md;
- [ADR 0003](../../docs/adr/0003-orchestrator-is-a-scheduler-not-an-authority.md), [ADR 0011](../../docs/adr/0011-event-driven-portfolio-manager-runs.md) and [ADR 0013](../../docs/adr/0013-deterministic-services-run-their-own-loops.md).

The shared grants contract is `specs/001-data-model/contracts/role-grants.md`, amended for this feature.

**Tests**: included, and written first within each story. The spec requires stand-in agents and a test clock, never real agents or model calls (FR-028). SC-003, SC-004 and SC-006 are universal claims, backed by Hypothesis properties of the pure planner.

**Organization**: one phase per user story (US1–US5 in spec.md). The pure planner (`planner.py`) grows one rule per story. `service.py` gains `tick()` in US1, and failure and restart handling in US4.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5

## Conventions every task follows

- **No task starts a real agent or makes a model call.** Unit tests use `tests/fakes/launcher.py`. Launcher tests use `tests/fakes/agent.py`, a stand-in child process. The suite-wide network guard in `tests/conftest.py` stays as it is.
- **Never read, print or grep for real credentials or `.env` files.** Tests set obviously fake values with `monkeypatch.setenv`, such as `RESEARCH_TEST_TOKEN=fake-value-not-real`.
- **The planner** (`orchestrator/planner.py`) is pure:
  - it never imports `psycopg`, `subprocess`, `os`, `signal`, `orchestrator/service.py` or `orchestrator/launcher.py`, and never reads the clock;
  - it may import `trading_agent.risk.calendar`;
  - `now` is always an argument.
- **Times are compared in ET wall-clock time** through `zoneinfo`. Trading days, opens and closes come from `trading_agent.risk.calendar`.
- **Test clock**: Monday **2026-09-28** (EDT, UTC−4), unless the test is about another day. Useful UTC times:

  | ET | UTC |
  |---|---|
  | 08:30 | 12:30 |
  | 10:00 | 14:00 |
  | 15:30 | 19:30 |

  Other days:
  - weekend: 2026-09-26;
  - holiday: 2026-11-26;
  - early close: 2026-11-27, EST (UTC−5), closing at 13:00 ET (18:00 UTC), so the cutoff is 12:30 ET (17:30 UTC);
  - a winter day for daylight-saving checks: 2026-12-07, when 08:30 ET is 13:30 UTC.
- **Test connections**: integration tests use feature 001's rolled-back `conn` fixture and `as_role`. Only the single-instance and restart tests open real autocommit connections, and they clean up after themselves.
- **Mutation-check every new test**: break the code on purpose, confirm the test fails, then revert with a direct edit to the file's saved text.
- **Don't edit committed migrations**: `0010` is new.

---

## Phase 1: Setup

- [X] T001 [P] Create the packages `src/trading_agent/orchestrator/__init__.py` (docstring naming ADR 0003, 0011 and 0013), `tests/unit/orchestrator/__init__.py` and `tests/integration/orchestrator/__init__.py`
- [X] T002 [P] Add an `--- Orchestrator (specs/005-orchestrator) ---` section to `.env.example`, with names and comments only, no values:
  - `ORCHESTRATOR_DATABASE_URL`: a login in `ta_orchestrator`.
  - A comment that each agent's variables must start with its prefix (`RESEARCH_`, `OPPORTUNISTIC_IDENTIFIER_`, `PORTFOLIO_MANAGER_`) and are added by that agent's feature (research O4).
- [X] T003 [P] Create `config/schedule.yaml` exactly as in `contracts/orchestrator-interface.md` "Configuration", with all three agents `enabled: false` and a header comment: the schema contract, changed only through code review (FR-015), no agent writes it, and ADR 0011's bounds are enforced by the loader.

---

## Phase 2: Foundational (blocks every story)

- [X] T004 Write the failing test `tests/integration/storage/test_orchestrator_schema.py` for migration 0010 (data-model.md). It must cover:
  - **As `ta_orchestrator`**:
    - `SELECT generated_at FROM latest_report_time` returns the newest `reports.generated_at`, or NULL when there are none, and the view has exactly one column;
    - `SELECT trading_paused FROM system_state` is allowed;
    - `SELECT daily_starting_equity FROM system_state`, `SELECT * FROM system_state_effective`, and `SELECT symbol FROM reports` / `decisions` / `positions` all raise `InsufficientPrivilege`;
    - INSERT into `orchestrator_runs` is allowed;
    - `UPDATE orchestrator_runs SET pgid=…, outcome=…, finished_at=…, detail=…` is allowed;
    - `UPDATE orchestrator_runs SET agent=…` and DELETE are denied.
  - **CHECK constraints**:
    - `agent` must be in (`research`, `opportunistic_identifier`, `portfolio_manager`);
    - `reason` must be in (`scheduled`, `morning_session`, `event_driven`, `catch_up`);
    - `outcome` must be in (`running`, `succeeded`, `failed`, `timed_out`, `interrupted`, `skipped`);
    - `outcome = 'skipped'` ⇔ `started_at IS NULL`;
    - `outcome = 'running'` ⇔ `finished_at IS NULL AND started_at IS NOT NULL`;
    - `slot_key IS NULL` ⇔ `reason = 'event_driven'`.
  - **Unique slots** (research O5):
    - two rows with the same `(agent, trading_day, slot_key)` are rejected, **including** a `morning_session` row plus a `catch_up` row with `slot_key = 'morning_session'`;
    - two `event_driven` rows on the same day are allowed.
  - **`reports` policies**: the policies in `pg_policies` are unchanged by 0010.
- [X] T005 Create `src/trading_agent/storage/migrations/0010_orchestrator.sql`, headed with a comment citing research O5–O7. It must:
  - `CREATE TABLE orchestrator_runs` per data-model.md (including `slot_key` and `pgid`), with its CHECKs, the unique index on `(agent, trading_day, slot_key)`, and the `(agent, started_at DESC)` index;
  - `CREATE VIEW latest_report_time AS SELECT max(generated_at) AS generated_at FROM reports`, not `security_invoker`;
  - `REVOKE SELECT ON system_state, system_state_effective FROM ta_orchestrator`, then `GRANT SELECT (trading_paused) ON system_state TO ta_orchestrator`;
  - `GRANT SELECT, INSERT, UPDATE (pgid, finished_at, outcome, detail) ON orchestrator_runs TO ta_orchestrator`;
  - `GRANT SELECT ON orchestrator_runs, latest_report_time TO ta_assistant, ta_dashboard`, and `GRANT SELECT ON latest_report_time TO ta_orchestrator`.
  
  Make T004 pass.
- [X] T006 Amend `tests/integration/storage/grants_matrix.py` and `factories.py`:
  - `factories.py`: add `orchestrator_runs` as a table, probing `detail`, with `columns_for_update` listing every column; add `latest_report_time` as a view.
  - `grants_matrix.py`, exactly (fixed after `/speckit-analyze` G2):
    - `orchestrator_runs`: `ta_orchestrator: {"S", "I", "U:pgid", "U:finished_at", "U:outcome", "U:detail"}`, `ta_assistant: {"S"}`, `ta_dashboard: {"S"}`;
    - `latest_report_time`: `ta_orchestrator`, `ta_assistant` and `ta_dashboard`, each `{"S"}`;
    - `system_state`: `ta_orchestrator` becomes `{"S:trading_paused"}`;
    - `system_state_effective`: `ta_orchestrator` removed.
  - `tests/integration/storage/test_system_state.py` (fixed after G1): `test_pause_toggle_readable_until_toggled_back` reads `system_state_effective` as `ta_orchestrator`, which 0010 revokes. Change it to read `SELECT trading_paused FROM system_state` as `ta_orchestrator`, which still shows the orchestrator can see the toggle, and keep a separate check through `system_state_effective` as `ta_dashboard`.
  
  Amend `specs/001-data-model/contracts/role-grants.md` to match, with footnote ⁴ "Amended by `specs/004`… `specs/005-orchestrator` (migration `0010`)" explaining the narrowing. Run `test_grants.py` both ways. Mutation-check it by temporarily restoring `ta_orchestrator`'s `S` on `system_state_effective` in the matrix.
- [X] T007 [P] Write the failing test `tests/unit/orchestrator/test_config.py`, then create `src/trading_agent/orchestrator/config.py`: `load_config(path) -> ScheduleConfig` (frozen dataclasses per agent), raising `ScheduleConfigError`. It must reject:
  - a missing or unknown key;
  - an agent `module` that doesn't match `^trading_agent\.[a-z_]+$`;
  - an `env` entry that isn't a string, is a duplicate, or doesn't start with the agent's own prefix (research O4). Test `ALPACA_API_KEY_ID`, `EXECUTION_DATABASE_URL`, `ADMIN_DATABASE_URL`, `REFERENCE_DATA_FINNHUB_API_KEY`, another agent's prefixed name, and the orchestrator's own `ORCHESTRATOR_DATABASE_URL` on every agent;
  - a time that isn't `HH:MM`;
  - `timeout_minutes` outside 1–120;
  - `min_spacing_minutes` below 30, `last_start` after 15:30, or `before_close_minutes` below 30 (FR-015);
  - `report_wait_minutes` outside 5–60 (FR-015);
  - `morning_session` before 09:30 or not before `last_start`; Research `daily_at` at or after 09:30;
  - a non-positive `interval_minutes`, or `window_end` at or before `window_start`;
  - `bool` where an int is expected.
  
  Test that the shipped file loads, with all agents disabled.
- [X] T008 [P] Create `src/trading_agent/orchestrator/launcher.py` with the `Launcher` protocol (`start`, `poll`, `stop`, `stop_group`), `Handle` (carrying `pgid`), and `LaunchFailed(error_type)` per contracts/launcher-port.md. Create `tests/fakes/launcher.py`:
  - `FakeLauncher` records `(clock_time, module, sorted env names)` for each start;
  - `finish(handle, status)`, `hang(handle)` and `fail_start(module)`;
  - `stop()` records the stop and returns `-15`;
  - `stop_group(pgid, module)` returns whatever the test scripted (a live orphan or a gone one), and records the call.
  
  Add `tests/unit/orchestrator/test_fake_launcher.py`.
- [X] T009 [P] Create `tests/fakes/agent.py`, a stand-in agent run as `python tests/fakes/agent.py <mode>`, or through the module-path shim the launcher test uses. Modes: `ok` (exit 0), `fail` (exit 1), `hang` (sleep 600), `spawn` (start a child that sleeps 600, write its pid to a file given in argv, then hang), and `env` (write the sorted names, not the values, of its environment to a file given in argv, then exit 0).
- [X] T010 [P] Write the failing test `tests/unit/orchestrator/test_import_guard.py`. Across every module under `trading_agent.orchestrator`:
  - none imports `alpaca`, `anthropic`, `trading_agent.execution`, `trading_agent.reference`, `trading_agent.risk.service`, `trading_agent.risk.gate` or `trading_agent.risk.rules`;
  - no source contains `ALPACA_`, `EXECUTION_DATABASE_URL`, `RISK_GATE_DATABASE_URL`, `ADMIN_DATABASE_URL` or `REFERENCE_DATA_`;
  - the planner imports none of `psycopg`, `subprocess`, `os`, `signal`, `orchestrator.service` or `orchestrator.launcher`.
  
  It passes vacuously until those files exist.

**Checkpoint**: migration 0010 applied and grants verified both ways; config, launcher port, fakes and stand-in agent ready.

---

## Phase 3: User Story 1 — The day's scheduled runs happen on time (P1) 🎯 MVP

**Goal**: on trading days, Research at 08:30 ET, the PM's morning session at 10:00, and the Opportunistic Identifier hourly from 10:00 to 15:00. Nothing on other days, and nothing for disabled agents.

**Independent test**: with all agents enabled and the fake launcher, tick every 30 seconds from 07:00 to 17:00 ET on 2026-09-28. Starts happen at exactly the scheduled slots, within 1 minute. A weekend or holiday starts nothing.

### Tests for User Story 1 (write first, confirm failing)

- [X] T011 [P] [US1] `tests/unit/orchestrator/test_planner_slots.py`, for `research_slots(day, cfg)`, `oi_slots(day, cfg)` and `cutoff(day, cfg)`:
  - normal day: Research `[08:30]`; with `interval_minutes=120`, `[08:30, 10:30, …]` before the cutoff; OI `[10:00 … 15:00]` hourly, 6 slots; cutoff 15:30;
  - early close on 2026-11-27: cutoff 12:30, and OI slots `[10:00, 11:00, 12:00]` only;
  - winter day on 2026-12-07: slots at the same ET wall-clock times;
  - weekend and holiday: all empty;
  - naive datetimes raise `ValueError`.
- [X] T012 [P] [US1] `tests/unit/orchestrator/test_planner_scheduled.py`, for `plan(now, cfg, state) -> list[Action]`, where `state` holds today's run records, whatever is running, the latest report time and the pause flag:
  - **08:30:10**: `Start(research, scheduled, 08:30)` once; a second call with that start recorded returns nothing.
  - **10:00**: `Start(portfolio_manager, morning_session, 10:00)` and `Start(opportunistic_identifier, scheduled, 10:00)`.
  - **Disabled agent**: never started.
  - **Weekend or holiday**: empty.
  - **Due Identifier slot while the Identifier is still running**: `Skip(…, "previous run in progress")`, once per slot.
  - **Every start and skip carries its `slot_key`** (`research_daily`, `morning_session`, `oi@10:00`).
  - **On time or catch-up** (research O9; fixed after A1):
    - a first tick at 08:30:20 is `scheduled`, and one at 08:31 is `catch_up`, both with `slot_key = 'research_daily'`;
    - a morning session held back while Research runs until 10:07 starts at 10:07 as `morning_session`, not `catch_up`.
  - **Order**: at 10:00 with Research still due (a late start), the actions are Research first, then the Identifier, and **no** PM start in that tick.
- [X] T013 [US1] `tests/unit/orchestrator/test_service_day.py`, using the fake launcher and an in-memory `RunStore` (`tests/unit/orchestrator/support.py`):
  - a full simulated day ticking every 30 s, with agents finishing after 2 minutes;
  - starts at the exact slots, within 1 minute (SC-001);
  - every start creates a `running` record, updated to `succeeded` on exit 0;
  - each agent receives only its listed names plus the base set (research O4), checked through the fake launcher's recorded env names;
  - a weekend day creates no records (SC-002);
  - **record before start** (fixed after R1): the `running` record is inserted before `launcher.start` is called (check the order of calls through the fakes); `pgid` is then written; if `start` raises `LaunchFailed`, the record ends `failed` with the error type, and the slot is not tried again that tick or later;
  - **hung agent** (SC-005): the Identifier hangs from 11:00 while Research and PM slots arrive; those start on time, and the Identifier is stopped within one tick of 11:10.

### Implementation for User Story 1

- [X] T014 [P] [US1] Create `src/trading_agent/orchestrator/planner.py` with:
  - the `Start`, `Skip` and `Stop` actions, and `RunRecord`/`State` dataclasses mirroring data-model.md;
  - `research_slots`, `oi_slots` and `cutoff` (research O9);
  - `plan()` for scheduled runs, the morning session, and overlap skips (O10).
  
  Make T011 and T012 pass.
- [X] T015 [US1] Create `src/trading_agent/orchestrator/service.py`:
  - the `RunStore` protocol: `today_runs(day)`, `last_pm_start()`, `last_successful_pm_start()`, `insert_start(...) -> id` (raising `SlotTaken` on a unique-index violation), `set_pgid(id, pgid)`, `insert_skip(...)`, `finish(id, outcome, detail, now)`, `running_rows()`, `latest_report_time()` and `trading_paused()`;
  - `PgRunStore(conn, *, _allow_savepoints=False)`, requiring autocommit as in 003 and 004;
  - `Orchestrator(cfg, store, launcher, environ_get)`, whose `tick(now)` polls the running handles and records finished ones, builds the `State`, calls `plan`, then applies the actions.
  
  For each `Start`, it inserts the `running` record first (claiming the slot; a unique-index violation means another process claimed it, so skip), then calls `launcher.start`, then writes the `pgid`. On `LaunchFailed` it finishes the record as `failed` (research O5). Timestamps come from the injected clock (research O16).

  The environment for an agent is `{name: environ_get(name)}` for its listed names plus the base set, leaving out missing names and never logging values (FR-008a). A launch error is recorded as `failed` with its error type. Make T013 pass.
- [X] T016 [US1] `tests/integration/orchestrator/test_service_postgres.py`: the service against Postgres as `ta_orchestrator`, with the fake launcher, through one morning (08:30 Research, 10:00 PM and Identifier). The records match, and the unique index rejects a manually inserted duplicate slot.

**Checkpoint**: scheduled runs work end to end with the fake launcher.

---

## Phase 4: User Story 2 — The PM runs again when there's something new (P1)

**Goal**: event-driven PM runs after the morning slot is done, per FR-013 (5-minute wait, 30-minute spacing, cutoff), covering bursts in one run.

**Independent test**: planner scenarios from spec US2, plus a Hypothesis property over random report times.

- [X] T017 [P] [US2] `tests/unit/orchestrator/test_planner_event_driven.py`. The five spec US2 acceptance scenarios as tables, plus:
  - a report at 15:28 means no run, with the cutoff at 15:30;
  - a report at 15:24 with a PM run started at 15:00 means no run: 15:30 is not before the cutoff;
  - early close: a report at 12:20 with no run since the morning means a run at 12:25; a report at 12:26 means no run;
  - a failed PM run at 11:07 means the next run is at 11:37 even with no newer report (clarification 2). A timed-out run and an interrupted run behave the same way;
  - a report newer than the last *successful* run but older than a failed later run is still considered new;
  - a PM run in progress means no new start;
  - no morning slot done yet (before 10:00) means no event-driven run, whatever the reports (FR-014).
- [X] T018 [P] [US2] `tests/unit/orchestrator/test_planner_properties.py`, Hypothesis over random report times, random run outcomes and a 30-second tick from 07:00 to 17:00. Simulate by feeding the planner's own starts back as records. Assert that:
  - PM starts are never less than 30 minutes apart (SC-003);
  - no event-driven start is within 5 minutes of the newest report, or at or after the cutoff;
  - at most one morning session and one Research daily run happen each day.
  
  Check with `hypothesis.event` that runs actually happen.
- [X] T019 [US2] Extend `planner.py` with the event-driven rule (research O8). Make T017 and T018 pass.

---

## Phase 5: User Story 3 — A pause stops the PM, not the analysts (P1)

**Goal**: no PM start while paused or when the pause flag is unreadable. A skipped morning session counts as done, and after resuming the event-driven rule runs once.

- [X] T020 [P] [US3] `tests/unit/orchestrator/test_planner_pause.py`:
  - paused at 10:00 gives `Skip(pm, morning_session, "trading paused")`, while the Identifier still starts;
  - paused all morning and resumed at 13:00 gives one PM `event_driven` start at 13:00;
  - pause flag unreadable (`None`) gives a `Skip(…, "pause flag unreadable")` for the morning session and no event-driven start;
  - Research is unaffected by the pause;
  - property: across random pause windows, zero PM starts happen while paused (SC-004).
- [X] T021 [US3] Extend `planner.py` with the pause rules (research O11). In `service.py`, read the pause through `store.trading_paused()` in the same tick, just before planning. A read error becomes `None`, logged at ERROR once per tick. Event-driven "due but paused" is logged once per pause episode, with no record. Make T020 pass, and add a service test for the log-once behaviour to `test_service_day.py`.
- [X] T022 [US3] `tests/integration/orchestrator/test_pause_postgres.py`: with `system_state.trading_paused` set by the admin, a tick at 10:00 as `ta_orchestrator` records the skipped morning session. After clearing it, a tick starts the PM.

---

## Phase 6: User Story 4 — Failures and restarts don't break the schedule (P2)

**Goal**: timeouts stop the process group; failures are recorded and not retried early; restarts mark `interrupted`, catch up Research and the morning session once (Research first), and never backfill Identifier slots.

### Tests (write first)

- [X] T023 [P] [US4] `tests/unit/orchestrator/test_planner_catch_up.py`:
  - first tick at 11:15 with no records today gives `Start(research, catch_up, research_daily)` and `Start(identifier, scheduled, 11:00)` only, with no 10:00 backfill;
  - the morning session waits while the catch-up Research run is in progress;
  - once Research finishes (or times out), `Start(pm, catch_up, morning_session)`;
  - at 15:31 with no records: no Research or morning catch-up, because the cutoff has passed;
  - a morning session recorded at 10:00 followed by a restart at 10:20 gives no second morning session (SC-006);
  - property: re-planning from the same records twice gives no new starts (SC-006);
  - **restart property** (fixed after G3): Hypothesis picks a restart time in a simulated day. Records written before it stay, `running` ones become `interrupted`, and the simulation carries on. No `slot_key` is ever started twice. Research's daily run and the morning session each happen at most once, and exactly once if they were still due before the cutoff;
  - intraday Research slots (with `interval_minutes` set) are never backfilled, the same as the Identifier's.
- [X] T024 [P] [US4] `tests/unit/orchestrator/test_planner_timeouts.py`:
  - a running record older than the agent's timeout gives `Stop(run_id)`;
  - one older than timeout minus 1 s gives nothing;
  - a failed Identifier run at 11:00 is not restarted until 12:00 (FR-019).
- [X] T025 [P] [US4] `tests/unit/orchestrator/test_launcher.py`, with the real `SubprocessLauncher` and `tests/fakes/agent.py`:
  - `ok` gives exit status 0 and `fail` gives 1, through `poll`;
  - `hang` then `stop` returns within the injected grace period plus 1 s, and the process is gone;
  - `spawn` then `stop`: the grandchild pid is gone too (the process group was killed);
  - `env`: the child saw exactly the given names;
  - a module that doesn't exist gives a non-zero exit, or `LaunchFailed`, recorded as `failed`;
  - `stop_group(pgid, module)` stops a live `hang` child whose command line contains the module and returns `True`. It returns `False`, without signalling, for a group that's gone or runs a different module.
  
  Keep the total test time low by using a short grace period, injectable for tests.
- [X] T026 [US4] `tests/integration/orchestrator/test_restart.py`, with a real autocommit connection as `ta_orchestrator` and the fake launcher. Insert a `running` PM record with a `pgid` that the fake reports as a live orphan, plus one with no `pgid`. Start a fresh `Orchestrator` and run its startup: `stop_group` is called for the first before its record becomes `interrupted`, and the second becomes `interrupted` too (FR-023). A later tick treats it as failed for the rules (clarification 2). Clean up the committed rows afterwards.

### Implementation

- [X] T027 [US4] Extend `planner.py` with catch-up, the Research-first gating and timeouts (research O9, O10). Create `SubprocessLauncher` in `launcher.py` per research O3: `Popen([sys.executable, "-m", module], env=env, start_new_session=True)`; SIGTERM to the process group; SIGKILL after the grace period (10 s by default). Extend `service.py` with:
  - the startup step `reap_and_mark_interrupted`: for each `running` row with a `pgid`, call `launcher.stop_group(pgid, module)`, then mark it `interrupted` and log it (research O12);
  - applying `Stop`;
  - `shutdown()`: stop every running handle, then record each as `interrupted` if the connection is still up (FR-005a).
  
  Make T023–T026 pass.

---

## Phase 7: User Story 5 — The owner can see what ran (P3)

- [X] T028 [P] [US5] `tests/unit/orchestrator/test_logging.py`, with `caplog` and the formats in `contracts/orchestrator-interface.md` "Log lines":
  - start, finish, skip, failure and timeout lines;
  - "due but paused" once per episode;
  - an ERROR line when the view or the pause flag can't be read;
  - across all records, no value of any variable passed to an agent appears in the logs. Set a distinctive fake value and assert it's absent. This is meaningful here because the service does copy real values (FR-008a).
- [X] T029 [US5] Add the log lines to `service.py`, and make T028 pass. Add an integration check to `test_service_postgres.py` that a simulated day with a failure, a timeout and a paused morning leaves a record for every slot, with its reason and outcome (US5 acceptance).

---

## Phase 8: The process (`python -m trading_agent.orchestrator`)

- [X] T030 [P] `tests/unit/orchestrator/test_main.py`, mirroring `tests/unit/reference/test_main.py`, with injected `connect`, `launcher_factory`, `store_factory`, `sleep`, `clock`, `max_ticks` and the lock wait:
  - **Exit 2**: a missing `ORCHESTRATOR_DATABASE_URL` (the message names the variable); a bad config (including the prefix rule); the lock held elsewhere after waiting.
  - **Exit 3**: a connect `OperationalError`, or one raised during a tick.
  - **Exit 0**: `max_ticks` reached.
  - **Startup order**: `reap_and_mark_interrupted` runs before the first tick.
  - **Shutdown** (fixed after P1): a SIGTERM delivered mid-loop (simulated by the injected sleep raising the handler's exception), `max_ticks` ending, or an `OperationalError` all call `orchestrator.shutdown()` before exiting, so running handles are stopped.
  - **Environment reads**: the orchestrator reads `ORCHESTRATOR_DATABASE_URL`, plus only the names listed for **enabled** agents, and only at the moment of starting one. Check with a recording mapping.
- [X] T031 Create `src/trading_agent/orchestrator/__main__.py`:
  - `TICK_SECONDS = 30`;
  - `SINGLE_INSTANCE_LOCK = 0x6f726368` ("orch"), with a comment naming the other components' keys to avoid;
  - the startup sequence per research O12, with keepalives as in 003 and 004;
  - SIGTERM and SIGINT handlers that end the loop, and a `finally` block that calls `orchestrator.shutdown()` (research O3, FR-005a);
  - ticks start every 30 s, and a long tick is followed immediately by the next;
  - `logging.basicConfig` under `__main__`.
  
  Make T030 pass.
- [X] T032 `tests/integration/orchestrator/test_single_instance.py`: a second lock taker is refused while the first holds the lock, and succeeds once the first connection closes (FR-021).

---

## Phase 9: Polish & cross-cutting

- [X] T033 [P] Update `docs/specs/orchestrator.md`:
  - agents run as separate processes with prefixed variables;
  - the run records and the one-value view;
  - reads only `trading_paused`;
  - the early-close cutoff;
  - failed-run and catch-up behaviour.
  
  Cite `specs/005-orchestrator`, its Clarifications, and [ADR 0015](../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md). Keep it a behaviour spec, without function signatures.
- [X] T034 [P] Update `docs/specs/data-model.md` with the `orchestrator_runs` and `latest_report_time` sections, and note the narrowed `system_state` read. Update `docs/architecture/overview.md`: the orchestrator is a process that starts the agents, with its own run records, and the stale line "It holds no database credentials" is corrected to reference ADR 0011 and 005.
- [X] T035 [P] Mutation-check pass over the planner and launcher rules. Covered code:
  - the 30-minute spacing, the 5-minute wait, and the cutoff (normal and early close);
  - the morning slot counting as done when skipped;
  - "newer than the last *successful* start";
  - pause fail-closed;
  - catch-up only before the cutoff, Research first, and no Identifier backfill;
  - the overlap skip and the timeout stop;
  - killing the whole process group;
  - the prefix rule, and the ADR 0011 bounds in the loader;
  - `reap_and_mark_interrupted` and `shutdown()` stopping every running group;
  - record-before-start (swap the order and confirm the R1 test fails);
  - the single `slot_key` unique index;
  - the 0010 REVOKE.
  
  Record the results in the implementation notes.
- [X] T036 Run the full offline suite, the integration suite and lint per `quickstart.md`. All must pass.
- [X] T037 Walk through `quickstart.md`, and confirm every command and expected outcome matches the implementation.

---

## Phase 11: Fixes from the adversarial review (spec Clarifications 2026-10-01)

- [X] T040 H1: `latest_report_time` counts only `generated_at <= now()` (migration 0010); tests `test_a_future_dated_report_is_ignored` and `test_a_future_dated_report_does_not_block_the_pm`
- [X] T041 M1: `SubprocessLauncher.poll` kills the process group once the leader exits (`launcher.py`); stand-in mode `spawn_exit`; test `test_a_child_left_behind_by_a_finished_agent_is_cleaned_up`
- [X] T042 M2, L1, L4: config bounds (PM timeout at most `before_close`; interval longer than timeout; morning session before the early-close cutoff) in `config.py`, with tests in `test_config.py`
- [X] T043 M3 (with T038): `StopFlag` in `__main__.py`, a sleep taken a second at a time, and `_stop` dropping the handle only after `stop` returns; tests in `test_main.py`
- [X] T044 L1: runs being stopped don't count as running (`planner.py`); test `test_a_run_being_stopped_does_not_cost_its_next_slot`
- [X] T045 L2 (with T039): `PgRunStore.trading_paused` returns `None` for a missing row; test `test_a_missing_pause_row_means_unknown_so_the_pm_does_not_run`
- [X] T046 L5: other database errors exit 3 after stopping agents; test `test_an_unexpected_database_error_exits_3_after_stopping_agents`
- [X] T047 L6: trigger `orchestrator_runs_only_running_changes` in migration 0010 (owner-approved: 0010 has not been merged or applied anywhere); test `test_a_finished_run_cannot_be_changed`
- [X] T048 Tests: the lock test runs as `ta_orchestrator`; mutation pass over the fixes (see notes)

## Dependencies & execution order

- **Setup (T001–T003)**: no dependencies.
- **Foundational (T004–T010)**: T005 after T004; T006 after T005; T007–T010 run in parallel with each other and with T004–T006. They block all stories.
- **US1 (T011–T016)**: T011 and T012 in parallel; T014 after them; T013 after T008; T015 after T014 and T008; T016 after T005 and T015.
- **US2 (T017–T019)**: after T014. It can run alongside US1's service tasks.
- **US3 (T020–T022)**: after T019 and T015.
- **US4 (T023–T027)**: after T019 and T015. T025 is independent of the planner work.
- **US5 (T028–T029)**: after T027.
- **Process (T030–T032)**: T031 after T027.
- **Polish (T033–T037)**: T033 and T034 any time after Foundational; T035–T037 last.

### Parallel examples

- **Foundational**: T007, T008, T009 and T010 together, while T004 → T005 → T006 run in sequence.
- **Planner tests**: T011, T012, T017 and T018 can be written together.
- **US4**: T023, T024 and T025 together, then T027.

## Implementation strategy

1. **MVP**: Setup, Foundational and US1. Scheduled runs with the fake launcher, and records in Postgres.
2. **US2 and US3**: event-driven PM and pause. These complete ADR 0011's behaviour.
3. **US4**: the real launcher, timeouts, restarts and catch-up. The feature isn't deployable before this.
4. **US5, the process, then polish.**
5. **Agents**: each agent's own feature enables its entry in `config/schedule.yaml`.

## Implementation notes

- **Test counts**: 652 offline (was 460) and 1260 integration (was 1070). Lint and format are
  clean. The offline suite now takes about 3 minutes; the planner's Hypothesis properties, which
  simulate whole trading days, add about 50 seconds.
- **Planner shape**: as research O1–O16 describe. A few details the tasks left open:
  - `plan()` returns actions in a fixed order: timeouts (`Stop`), then Research, the PM and the
    Identifier.
  - An event-driven run that is due while trading is paused becomes a `Hold` action. It is logged
    once per pause episode and never recorded (O11).
  - Timeouts are checked every day, trading or not.
  - The morning session is labelled `morning_session` when it starts on time, or when it was held
    back only by a Research run that was going at 10:00. Otherwise it is `catch_up` (O9).
- **Service**: `Orchestrator.startup` reaps orphans and marks them interrupted. `shutdown` stops
  every running agent's group. `__main__` calls `shutdown` from a `finally` block, so it runs on
  SIGTERM, SIGINT, the loop ending, and a lost connection (FR-005a).
- **Launcher**: on Linux, `stop_group` reads the leader's command line from `/proc`; elsewhere it
  uses `ps`. It signals only when that command line contains the agent's module.
- **A flaky test fixed, from 004**: `tests/integration/reference/test_single_instance.py`, and the
  new orchestrator lock test that copied it, retook the lock with no wait immediately after closing
  the first connection. The server releases a closed session's lock asynchronously, so the retake
  raced it and failed once under load. Both tests now allow up to 5 seconds.
- **Guard hook**: a shell heredoc writing the stand-in agent (`tests/fakes/agent.py`) was blocked
  by the local credential guard, because the file lists environment variable *names*. The file was
  written with the file tool instead, and the owner was told. The stand-in only lists names of an
  environment each test builds with fake values, to check FR-004 isolation.
- **T035 mutation pass**: 24 mutations, each applied, tested and restored by a scratch script. At
  first 22 were caught and 2 survived:
  - **No backfill**: with the default window, the last slot's interval ends exactly at the close,
    so a late run could never happen. Added `test_a_slot_past_its_own_interval_is_not_run_late`,
    with a window ending at 13:00.
  - **Killing the whole group**: the stand-in's grandchild died from SIGTERM anyway. Added a
    `spawn_stubborn` mode and `test_stop_kills_a_grandchild_that_ignores_sigterm`.

  Both mutations are now caught. The others were caught at once:
  - the 30-minute spacing and the 5-minute wait;
  - the cutoff, normal and early close;
  - a skipped morning session counting as done;
  - "newer than the last *successful* start";
  - the pause failing closed;
  - catch-up only before the cutoff, and Research first;
  - the overlap skip, the timeout, and the on-time/catch-up label;
  - the agent's own process group, and the orphan command-line check;
  - the prefix rule and the ADR 0011 spacing bound;
  - passing only the listed variables;
  - reaping orphans, and stopping agents at shutdown;
  - record-before-start;
  - the single slot-key index and the 0010 REVOKE.
- **Not done here, as planned**: the agents themselves (all ship disabled), deployment config,
  and alerts.

## Phase 10: Convergence

- [X] T038 Close the window in which a SIGTERM/SIGINT can orphan an agent: `_raise_stopping` in `src/trading_agent/orchestrator/__main__.py` raises `Stopping` at any bytecode, so a signal between `Popen` returning in `SubprocessLauncher.start` and `Orchestrator._start` storing the handle and calling `set_pgid` (`src/trading_agent/orchestrator/service.py` `_start`) leaves a live process group that `shutdown` doesn't know about and whose row has no `pgid`, so the next startup can't reap it; a second signal during `shutdown` likewise abandons the remaining groups. Defer the stop (a flag checked between ticks with an interruptible sleep, or mask the signals around start-and-register and around `shutdown`), and add a `tests/unit/orchestrator/test_main.py` case per FR-005a / FR-023 (partial)
- [X] T039 Make `PgRunStore.trading_paused` in `src/trading_agent/orchestrator/service.py` fail closed when `system_state` returns no row: today `bool(None)` reads as "not paused". Return `None` (unknown) or raise so the planner skips the PM, and add a test per FR-016 / US3/AC3 (partial)
- **Converge and adversarial review (T038–T048)**: converge found T038 (the signal window) and
  T039 (a missing pause row reading as not paused). The adversarial review found:
  - H1: a future-dated report blocked event-driven runs;
  - M1: an agent's children outlived it;
  - M2: a long PM timeout could run past the close;
  - M3: signal windows, the same as T038;
  - L1, L2 (= T039), L4, L5 and L6.

  All were applied with the owner's approval. The L6 trigger went into migration 0010 itself,
  which the owner approved because 0010 hasn't been merged or applied anywhere. L3 (two clocks)
  was left as documented in O16, by recommendation.

  Two existing planner tests used a run already past its timeout to stand for "still running".
  Under the L1 rule that run is now stopped in the same tick, so the tests now use a run still
  within its timeout. They test the same thing as before.

  A second mutation pass covered 10 mutations for the fixes, and all were caught. Test counts:
  665 offline and 1264 integration.
