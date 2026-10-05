# Implementation Plan: Observe-Only Deployment

**Branch**: `010-observe-only-deployment` | **Date**: 2026-10-05 (revised after analyze) | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/010-observe-only-deployment/spec.md`

## Summary

Deploy the system to Railway as four services (`orchestrator` with Research and the PM, `risk-gate`, `reference-data`, `execution`) and a managed Postgres.

**Trading stays off** because the pause flag is set before any service starts, and the paper account is flat before Execution first starts. A pre-open check confirms both (research R0, R9). Execution still records the account snapshots the PM and gate need. While paused, the gate approves no buy (`trading_paused`, or `market_closed` / `decision_stale` when those apply first), so observation shows the PM's real decisions and their verdicts, never an approved buy (owner accepted). A new reviewed setting, `portfolio_manager.run_while_paused`, lets the PM run while paused (R8).

**The project is described** in Railway's infrastructure-as-code file, because `railway.json` is deprecated (R1). It builds with Railpack, pinned to Python 3.12, with an editable install (R2), and deploys only from `release/prod` (R10).

**New code** is small:
- the orchestrator setting (loader and planner);
- a storage-layer login command (R6);
- a guard test for the deployed shape (R11).

**The owner runbook** covers setup, the flat-account precondition, release, the pre-open and post-deploy checks (R12), switching trading on (setting off first, then unpause after the close; R7, R8) and switching it off (R8).

## Technical Context

**Language/Version**: Python 3.12 (the application). TypeScript only for `.railway/railway.ts`, evaluated by the Railway CLI on the owner's machine, never deployed.

**Primary Dependencies**: existing (psycopg 3.2, PyYAML, exchange-calendars, alpaca-py, anthropic). New: the `railway` npm package, a dev-only tool under `.railway/`. No new Python dependency.

**Storage**: the shared Postgres. No schema change ([data-model.md](data-model.md)).

**Testing**: pytest (offline and integration), plus `scripts/lint.sh`.

**Target Platform**: Railway (Railpack build, Linux containers, working directory = repository root), Railway-managed Postgres.

**Project Type**: deployment configuration, one orchestrator setting, a small CLI command, and documentation.

**Performance Goals**: none new.

**Constraints**:
- Broker keys only on `execution`. The admin credential only in the owner's shell.
- Paused before any service starts. Flat paper account before Execution's first start. Switch-on only after the close.
- No change to risk limits, sizing or order logic (FR-014).

**Scale/Scope**: one Railway environment, 5 resources, 8 database logins.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
|---|---|---|
| I. Deterministic trade path | Execution and the gate are unchanged. Every order still passes the gate, and Execution re-derives its ceilings. | Pass |
| II. Analysts propose, PM decides | Unchanged. | Pass |
| III. Least privilege at the database | One login per component in its own group role. Broker keys only on Execution's service. The admin credential is in no service. The owner logins reuse existing roles with no new grant. The orchestrator carries its agents' logins (ADR 0015). | Pass |
| IV. One automatic hard stop; manual pause outside agents | The pause is the observe-only switch, changed only by the owner's control login, unreachable from any agent. No approval gate is added. The breaker is untouched. | Pass |
| V. Spec-and-ADR-first | ADR 0021 (deployment shape, observe-only, and the `run_while_paused` reversal of spec 005's pause behaviour) and a PATCH amendment of the deployment bullet land before code. Spec 005 is updated after the ADR. | Pass, once ADR 0021 lands first |
| VI. Paper only | Execution's existing paper check is unchanged. The runbook states it. | Pass |
| VII. Assistant and dashboard read-only | Not built. The owner logins use the dashboard's roles. | Pass |
| Tech & Deployment Constraints | The text says "`railway.json` / `railway.web.json` split" and "a worker service for the orchestrator and agents". Railway no longer accepts `railway.json` for new services (R1), and this plan uses one worker service per process. | **Deviation, justified below** |

**Post-design re-check**: unchanged. No role, table, grant or credential category is added. The new data flow is none: the PM now also runs while paused, reading the same inputs. ADR 0021 records the deployment mechanism and the orchestrator setting, and the constitution's deployment bullet is amended to match (1.1.0 → 1.1.1).

## Project Structure

### Documentation (this feature)

```text
specs/010-observe-only-deployment/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── service-layout.md
│   ├── logins-command.md
│   └── observe-setting.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
.python-version                          # new: 3.12 (R2)
requirements.txt                         # new: "-e ." (R2)
.railway/
├── railway.ts                           # new: postgres + 4 services, preserve() names (R1, R3)
├── package.json, package-lock.json      # new: the railway npm package, dev-only
└── README.md                            # new: plan/apply pointer to the runbook
config/schedule.yaml                     # + portfolio_manager.run_while_paused: true (R8)
src/trading_agent/orchestrator/config.py # + the new required key
src/trading_agent/orchestrator/planner.py# + honour it for a known pause only
src/trading_agent/storage/logins.py      # new: the login command (R6)
tests/unit/orchestrator/test_config.py, test_planner_pause.py   # extended
tests/unit/storage/test_logins.py        # new
tests/unit/deploy/test_deployed_shape.py # new: the guard test (R11)
tests/integration/storage/test_logins.py # new
tests/integration/storage/test_observe_queries.py               # new
docs/operations/deployment.md            # new: owner runbook
docs/operations/observe-queries.sql      # new: read-only queries, and the pre-open and post-deploy checks (R12)
docs/adr/0021-railway-deployment-as-code-observe-only-first.md  # new, before code
.specify/memory/constitution.md          # amended deployment bullet (PATCH)
docs/architecture/overview.md            # "Deployment shape" updated after ADR 0021
specs/005-orchestrator/spec.md           # pause section references ADR 0021's setting
.env.example                             # regrouped per service
.gitignore                               # .railway/node_modules/
```

**Structure Decision**: single project. The login command sits in `storage`, beside `migrate`. The setting stays inside `orchestrator`. The import layering is untouched.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Constitution names `railway.json` and one worker service | Railway refuses `railway.json` for new services, and stops reading it on 2026-12-01. One service per process keeps each process's variables apart (Clarify Q1). | Following the letter would be impossible (no `railway.json`) or would put broker keys next to the agents' keys, which conflicts with Principle III. |
| A TypeScript file in a Python repository | Railway's infrastructure as code is generally available only in TypeScript (owner chose A). | `.railway/railway.py` is beta. |
| PM runs while paused, reversing spec 005's pause behaviour | Observation needs decisions, and Execution must run (for snapshots), so trading must be held off by the pause. | Leaving Execution undeployed yields no decisions (R0). A no-submit mode in Execution is new order logic. |
