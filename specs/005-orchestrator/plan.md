# Implementation Plan: Orchestrator

**Branch**: `005-orchestrator` | **Date**: 2026-09-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-orchestrator/spec.md`

## Summary

`python -m trading_agent.orchestrator` is a 30-second loop that starts Research, the Opportunistic Identifier and the Portfolio Manager as separate processes on their cadences ([ADR 0003](../../docs/adr/0003-orchestrator-is-a-scheduler-not-an-authority.md), [ADR 0011](../../docs/adr/0011-event-driven-portfolio-manager-runs.md)).

- **Decisions**: a pure planner decides what is due, from the calendar, the recorded runs, the latest report time and the pause flag (research O1, O8–O11).
- **Starting agents**: a launcher runs each agent in its own process group, with a timeout and only its own prefixed variables (O3, O4).
- **Records**: every run and skip goes into `orchestrator_runs`, which makes restarts safe (O5, O12).
- **Migration 0010** adds the run table and a one-value `latest_report_time` view. It also narrows `ta_orchestrator`'s read of `system_state` to the pause flag (O5–O7).
- **Agents**: all three ship disabled.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: psycopg 3.2, PyYAML, exchange-calendars (via `trading_agent.risk.calendar`), the stdlib `subprocess`, `os` and `signal`. No new dependency.

**Storage**: the shared Postgres. `orchestrator_runs` is new, and so is the `latest_report_time` view. `system_state.trading_paused` is read only.

**Testing**: pytest with hypothesis. The planner, service and loader are tested offline. The real launcher is tested with stand-in child processes. Migration 0010 and the permissions get integration tests.

**Target Platform**: a Linux long-running process in the Railway worker service (not configured yet).

**Project Type**: a deterministic background service in the existing `trading_agent` package.

**Performance Goals**: a start within 1 minute of its slot (SC-001); a hung agent stopped within 1 minute of its timeout (SC-005).

**Constraints**:
- no model calls, and no reads of report contents or trading data;
- each agent gets only its own prefixed variables;
- ADR 0011's spacing and cutoff can't be loosened.

**Scale/Scope**: 3 agents, at most about 25 runs a day. One row per run.

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | The orchestrator isn't in the order path. It holds no broker credential, and the prefix rule makes passing one to an agent impossible. It never sees a decision or verdict. | Pass |
| II. Analysts propose, PM decides | It only sequences agents and can't alter, approve or block their output (FR-003). It never reads report contents. | Pass |
| III. Least privilege at the database | Its own role. It writes only its own run table, reads a one-value view, and reads **only** `trading_paused` (0010 narrows the broader 0005 grant). Each agent's variables are isolated by prefix. Enforced by the both-ways grants test. | Pass |
| IV. Autonomous, one hard stop | No approval gate is added. The pause is the owner's manual toggle, only read here; the orchestrator can't set it. | Pass |
| V. Spec and ADR first | Covered by ADRs 0003, 0011 and 0013 (own loop), plus the new [ADR 0015](../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md) (agents as processes; their credentials on the orchestrator's service, each passed only its own). The new grant was foreseen by ADR 0011, and narrowing an existing grant needs no ADR. `docs/specs/orchestrator.md` and `data-model.md` are updated to reference 005. | Pass |
| VI. Paper only | Not affected. | Pass |
| VII. Assistant and dashboard read-only | They get SELECT on the run records and the new view. No new writes. | Pass |

Re-check after design: all pass. The design adds one table, one view and one config file. Its only write is to its own table, and one existing grant is **narrowed**.

**Technology note**: ADR 0009 describes an "APScheduler-style in-process scheduler". A tick loop that works out what's due from the database is in-process and scheduler-like, and it avoids a second, in-memory job store (research O2).

## Project Structure

### Documentation (this feature)

```text
specs/005-orchestrator/
├── spec.md
├── plan.md                        # this file
├── research.md                    # O1–O16
├── data-model.md                  # orchestrator_runs, latest_report_time, grants delta
├── quickstart.md
├── contracts/
│   ├── orchestrator-interface.md  # process, exit codes, config, agent contract, outcomes, logs
│   └── launcher-port.md
├── checklists/requirements.md
└── tasks.md                       # /speckit-tasks
```

### Source Code (repository root)

```text
src/trading_agent/
├── orchestrator/
│   ├── __init__.py
│   ├── __main__.py      # loop, startup sequence, exit codes (O12)
│   ├── config.py        # config/schedule.yaml loader, bounds, prefix rule (O4, O13)
│   ├── planner.py       # pure: slots, cutoff, event-driven rule, catch-up, pause (O8–O11)
│   ├── launcher.py      # Launcher protocol + SubprocessLauncher (O3)
│   └── service.py       # tick: read state, plan, launch/stop, record (O1, O5)
└── storage/migrations/
    └── 0010_orchestrator.sql   # table, view, grants, system_state narrowing (O5–O7)

config/schedule.yaml     # all agents disabled

tests/
├── fakes/agent.py       # stand-in child process (exit 0/1, hang, spawn child, print env names)
├── fakes/launcher.py
├── unit/orchestrator/   # planner (tables + properties), config, service, launcher, main, import guard
└── integration/
    ├── orchestrator/    # restart/interrupted, duplicate slots, single instance, service against Postgres
    └── storage/         # grants_matrix + role-grants.md amended; view tests

docs/specs/orchestrator.md, docs/specs/data-model.md, docs/architecture/overview.md
specs/001-data-model/contracts/role-grants.md   # amended by 005
.env.example                                    # ORCHESTRATOR_DATABASE_URL
```

**Structure Decision**: a new `trading_agent.orchestrator` package, built like `execution` and `reference`. It imports `trading_agent.risk.calendar` and `storage.db.require_env`, and nothing from any agent or trading component. An import-guard test enforces that.

## Things flagged for the owner

- **A grant is narrowed.** Since migration 0005, `ta_orchestrator` has been able to read the whole `system_state` row, including the daily starting equity and the halt state. That is account data the orchestrator must never read (FR-002, ADR 0003). Migration 0010 cuts it to the `trading_paused` column only. This tightens a permission; it doesn't loosen one.
- **No risk limit, sizing or order logic changes.** ADR 0011's spacing and cutoff are enforced as minimums in the config loader.
- **Variable naming for agents.** Each agent's variables must start with its prefix (`RESEARCH_`, `OPPORTUNISTIC_IDENTIFIER_`, `PORTFOLIO_MANAGER_`). Agents will read, for example, `RESEARCH_ANTHROPIC_API_KEY`. This also covers the Identifier's future Qwen key.
- **Agents never outlive the orchestrator unsupervised** (fixed after `/speckit-analyze` P1). On shutdown or redeploy it stops every running agent's process group. After a crash, its next startup stops any recorded group still running that agent before marking the run interrupted (research O3, O12).

## Complexity Tracking

No constitution violations to justify.
