# Implementation Plan: Shared Data Model

**Branch**: `001-data-model` | **Date**: 2026-09-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-data-model/spec.md`

## Summary

Stand up the single shared Postgres knowledge base every agent and service reads and writes:
nine tables, two computed views, ten `NOLOGIN` group roles, and a grants matrix enforced by the
database (table/column grants plus row-level security on `reports`). Schema changes ship as
forward-only numbered SQL migrations applied by a separate admin step, so no component holds a
credential able to change its own permissions. The grants matrix is the feature's contract and
drives a test suite that assumes every role and asserts both what it can and cannot do.

Mapping the spec onto a real schema surfaced three gaps, resolved in [research.md](research.md)
and reflected back into `spec.md` and `docs/specs/data-model.md`:
- account cash/equity had no storage location → `account_snapshots` (R8)
- `report_ids uuid[]` can't be foreign-keyed → `decision_reports` junction table (R7)
- a stored halt boolean needs someone to reset it → store the date it fired, compute "active" (R9)

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `psycopg[binary]` 3.2 (no ORM); `pytest` 8 (dev)

**Storage**: PostgreSQL 16 (Railway managed in production; Docker locally). PG15+ required for
`security_invoker` views.

**Testing**: pytest. Offline suite needs no database. Integration suite (marker `integration`) runs
against a disposable Postgres via `TEST_DATABASE_URL`, rolls back every transaction, skips cleanly
when unconfigured.

**Target Platform**: Linux containers on Railway (worker + web services connect as separate roles)

**Project Type**: Single Python project (`src/` layout); this feature is its storage foundation

**Performance Goals**: None beyond defaults. Volume is tens of rows per trading day per table.

**Constraints**: Components never hold DDL-capable credentials; no secrets in the repo; every write
restriction enforced by the database, not application code (FR-017)

**Scale/Scope**: 9 tables, 2 views, 10 group roles, ~2 migrations

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this design satisfies it |
|---|---|---|
| I. Deterministic Trade Path | PASS | Only `ta_execution` can write `orders`; a composite foreign key makes an order for an unapproved verdict impossible (R12). No role but Execution's will ever hold a broker credential — this feature grants none. |
| II. Analysts Propose, PM Decides | PASS | Analysts are insert-only on `reports`, restricted to their own `agent` value by RLS. Only `ta_portfolio_manager` writes `decisions` and `decision_reports`, so every decision records the reports it drew on. |
| III. Least-Privilege at the Database | PASS | Each component's reads and writes are derived from its own spec (R4), narrower than "broad by default" where the spec disclaims access. Grants live in migrations, tested per cell. |
| IV. Autonomous Operation, One Hard Stop | PASS | Halt stored as the date it fired; clears itself at the next trading day with no write (R9). `trading_paused` writable only by `ta_dashboard_control` — no agent role can touch it. |
| V. Spec-and-ADR-First | PASS, with spec updates | R7/R8/R9 change representation and fill a storage gap; `spec.md` and `docs/specs/data-model.md` are updated alongside this plan. No ADR: no new role, flow, dependency, or credential category — `account_snapshots` stores data the accepted PM/Risk Gate/journal specs already read. |
| VI. Paper Trading, US Equities | N/A | Enforced by Execution and the Risk Gate, not the schema. |
| VII. Assistant & Dashboard Read-Only | PASS | `ta_assistant` and `ta_dashboard`: SELECT only. `ta_dashboard_control`: one column of one row. |
| Tech & Deployment Constraints | PASS | Python, psycopg, Postgres as sole durable store, Railway. Login passwords created out-of-band (R3). |
| Development Workflow | PASS | Foundation-first (this is feature 1). Integration tests cover every grant (R14). |

**Post-design re-check (after Phase 1)**: unchanged — all PASS. No Complexity Tracking entries.

## Project Structure

### Documentation (this feature)

```text
specs/001-data-model/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R15
├── data-model.md        # Phase 1: tables, fields, constraints
├── quickstart.md        # Phase 1: validation guide
├── contracts/
│   ├── role-grants.md   # Phase 1: roles and the grants matrix (drives the test suite)
│   └── views.md         # Phase 1: reports_with_status, system_state_effective
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
pyproject.toml                         # package metadata, deps, pytest + ruff config
.env.example                           # ADMIN_DATABASE_URL, TEST_DATABASE_URL (names only)

src/trading_agent/
├── __init__.py
└── storage/
    ├── __init__.py
    ├── db.py                          # connect(url): commit on success, rollback on error
    ├── migrate.py                     # forward-only runner; `python -m trading_agent.storage.migrate`
    └── migrations/
        ├── 0001_tables_and_views.sql  # 9 tables, constraints, indexes, 2 views, system_state seed
        └── 0002_roles_and_grants.sql  # group roles, table/column grants, RLS policies

tests/
├── unit/
│   └── storage/test_migrate_ordering.py    # version discovery/ordering, no database
└── integration/
    ├── conftest.py                    # fresh schema per session; rolled-back connection; set_role helper
    └── storage/
        ├── grants_matrix.py           # the matrix from contracts/role-grants.md, as data
        ├── test_grants.py             # every cell: allowed succeeds, unlisted rejected
        ├── test_constraints.py        # CHECKs, composite FK, uniqueness
        ├── test_views.py              # computed status, date-based halt
        └── test_migrate.py            # applies once, re-run is a no-op
```

**Structure Decision**: Single `src/`-layout package `trading_agent`, mirroring `trading-bot`'s
`src/trading_bot/storage/`. Later features add sibling packages (`risk/`, `execution/`, `agents/`,
`web/`) beside `storage/`. Two migrations rather than one keep "what the data is" reviewable
separately from "who may touch it" — a grant change diffs only `0002` or a later grants migration.

## Complexity Tracking

No constitution violations to justify.
