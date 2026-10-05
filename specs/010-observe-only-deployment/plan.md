# Implementation Plan: Observe-Only Deployment

**Branch**: `010-observe-only-deployment` | **Date**: 2026-10-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/010-observe-only-deployment/spec.md`

## Summary

Deploy the system to Railway as three services (`orchestrator` with Research and the PM, `risk-gate`, `reference-data`) and a managed Postgres. Execution is absent, so no order can be placed while the owner watches real decisions and verdicts.

The project is described in Railway's infrastructure-as-code file, because `railway.json` is deprecated (research R1). The services build with Railpack and pinned Python 3.12, with an editable install (R2), and deploy only from `release/prod` (R10).

New code is small:
- a storage-layer login command that creates every component's login and two owner logins (R6);
- a guard test that keeps broker and admin credentials out of the service file (R11).

The owner runbook covers setup, release, observation queries, and switching trading on (only after the close, R7) and off (R8).

## Technical Context

**Language/Version**: Python 3.12 (the application). TypeScript only for `.railway/railway.ts`, evaluated by the Railway CLI on the owner's machine, never deployed.

**Primary Dependencies**: existing (psycopg 3.2, PyYAML, exchange-calendars, alpaca-py, anthropic). New: the `railway` npm package, a dev-only tool under `.railway/`. No new Python dependency.

**Storage**: the shared Postgres. No schema change ([data-model.md](data-model.md)).

**Testing**: pytest (offline and integration, as today), plus `scripts/lint.sh`.

**Target Platform**: Railway (Railpack build, Linux containers), Railway-managed Postgres.

**Project Type**: deployment configuration, plus a small CLI command and documentation.

**Performance Goals**: none new. The loops keep their existing cadences (60 s passes and ticks; schedule.yaml).

**Constraints**:
- No broker key, admin credential or other component's login in any service (SC-003).
- The admin credential lives only in the owner's shell.
- Switch-on happens only after the close (R7).
- No change to risk limits, sizing, order logic or the schedule (FR-014).

**Scale/Scope**: one Railway environment (`production`), 4 resources, 8 database logins.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
|---|---|---|
| I. Deterministic trade path | No code on the trade path changes. Execution isn't deployed, so no order path exists at all while observing. | Pass |
| II. Analysts propose, PM decides | Unchanged. Research and the PM run as built. | Pass |
| III. Least privilege at the database | One login per component, each in exactly its own group role. Broker keys are on no service. The admin credential is in no service. The two owner logins reuse existing roles with no new grant. | Pass |
| IV. One automatic hard stop; manual pause outside agents | No approval gate is added. The pause stays a plain toggle the owner sets with their own control login, unreachable from any agent. | Pass |
| V. Spec-and-ADR-first | See below: the deployment shape needs ADR 0021, and the constitution's deployment bullet needs a PATCH amendment, before code. | Pass, once ADR 0021 lands first |
| VI. Paper only | Execution's existing startup refuses a non-paper endpoint. Switch-on keeps it unchanged. | Pass |
| VII. Assistant and dashboard read-only | Not built. The owner's read login uses the dashboard's read-only role. | Pass |
| Tech & Deployment Constraints | The text says "mirroring `trading-bot`'s `railway.json` / `railway.web.json` split" and "a worker service for the orchestrator and agents". Railway no longer accepts `railway.json` for new services (R1), and this plan uses three worker services rather than one (Clarify Q1). | **Deviation, justified below** |

**Post-design re-check**: unchanged. The design adds no role, table, grant, credential category or data flow. The deviation is wording about the deployment mechanism, recorded in ADR 0021, and the constitution is amended to match (Governance: an ADR plus a Sync Impact Report, version 1.1.0 → 1.1.1, PATCH).

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
│   └── logins-command.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
.python-version                         # new: 3.12 (R2)
requirements.txt                        # new: "-e ." (R2)
.railway/
├── railway.ts                          # new: postgres + 3 services, preserve() names (R1, R3)
├── package.json                        # new: the railway npm package, dev-only
└── README.md                           # new: plan/apply pointer to the runbook
src/trading_agent/storage/
└── logins.py                           # new: the login command (R6)
tests/unit/storage/test_logins.py        # new
tests/unit/deploy/test_observe_only.py   # new: the guard test (R11)
tests/integration/test_logins.py         # new
docs/operations/deployment.md           # new: owner runbook (setup, release, observe, switch on/off)
docs/operations/observe-queries.sql     # new: read-only queries for ta_owner_read_login
docs/adr/0021-railway-deployment-as-code-observe-only-first.md   # new, before code
.specify/memory/constitution.md         # amended deployment bullet (PATCH)
docs/architecture/overview.md           # "Deployment shape" updated after ADR 0021
.env.example                            # regrouped per service; owner-only variables marked
.gitignore                              # .railway/node_modules/
```

**Structure Decision**: single project. The login command sits in `storage`, beside `migrate`, the other admin-credential command, so the import layering is untouched. Deployment files sit at the root and in `.railway/`, where Railpack and the Railway CLI look for them.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Constitution names `railway.json` and one worker service | Railway refuses `railway.json` for new services and stops reading it on 2026-12-01. Three services keep each process's variables apart (Clarify Q1). | Following the letter would either be impossible (no `railway.json`) or put every component's keys in one service, which conflicts with Principle III. |
| A TypeScript file in a Python repository | Railway's infrastructure as code is generally available only in TypeScript. | `.railway/railway.py` is beta, and Railway says "helper names may change", which is risky for the file that defines what is live. |
