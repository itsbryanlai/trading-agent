---

description: "Task list for feature 010, observe-only deployment"
---

# Tasks: Observe-Only Deployment

**Input**: Design documents from `specs/010-observe-only-deployment/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Included. `CLAUDE.md` requires tests for every logic change. Each new test is mutation-checked: break the code, see the test fail, then restore the file by editing it (never `git checkout` or `git stash`).

**Organization**: by user story. US2 (database setup) comes before US1 (services), because the services need the logins US2 creates. Both are P1.

**Rules for every task**: atomic commits in the `git log` style (`Area (feature 010): what changed (T0NN)`), staged by explicit path. Run Python with `PYTHONPATH=src`. Run `PYTHONPATH=src scripts/lint.sh` and the relevant tests after any logic change. Read no `.env` file. Touch no risk limit, sizing rule, order logic or `config/schedule.yaml` (FR-014).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story the task belongs to (US1–US4)

---

## Phase 1: Setup (decision records, before any code)

**Purpose**: Constitution V. The deployment shape gets its ADR before code (plan, Complexity Tracking).

- [ ] T001 Write `docs/adr/0021-railway-deployment-as-code-observe-only-first.md` (status Accepted, using the template in `docs/adr/README.md`) recording these decisions:
  - Railway infrastructure as code in TypeScript `.railway/railway.ts`, replacing the deprecated `railway.json` (research R1);
  - one service per process (R3);
  - the setup and login commands run from the owner's machine (R5, R6);
  - deploys only from `release/prod` (R10);
  - observe-only first, with Execution undeployed (spec FR-008);
  - switch-on only after the close (R7);
  - switch-off rules (R8).

  Add its row to `docs/adr/README.md`.
- [ ] T002 Amend `.specify/memory/constitution.md`'s "Technology & Deployment Constraints" Railway bullet to name infrastructure as code and one worker service per process, citing ADR 0021. Bump the version 1.1.0 → 1.1.1 (PATCH), update **Last Amended**, and add a Sync Impact Report. Leave every principle unchanged.
- [ ] T003 Update the "Deployment shape" section of `docs/architecture/overview.md`. Replace "Railway worker service running the orchestrator + agents, Nixpacks build" with the three services, the Railpack build, `.railway/railway.ts` and `release/prod`, citing ADR 0021. Note that Execution is added only at switch-on.

**Checkpoint**: the decisions are recorded. Code may start.

---

## Phase 2: Foundational (build files every service needs)

- [ ] T004 [P] Create `.python-version` containing `3.12` (research R2: Railpack otherwise defaults to 3.13.2).
- [ ] T005 [P] Create `requirements.txt` containing the single line `-e .`, with a one-line comment explaining why it must be editable: the config loaders resolve `config/` from `Path(__file__).parents[3]`, and the orchestrator's launcher gives agents no `PYTHONPATH` (research R2). Confirm that `.venv/bin/pip install -r requirements.txt --dry-run` resolves.
- [ ] T006 [P] Add `.railway/node_modules/` to `.gitignore`.

**Checkpoint**: the repository builds as an editable Python 3.12 install.

---

## Phase 3: User Story 2 – A fresh database is built correctly and safely (P1)

**Goal**: one owner-run command creates every login, with exactly its group role's permissions (FR-004, FR-004a, FR-004b; contract [logins-command.md](contracts/logins-command.md)).

**Independent Test**: on a throwaway Postgres, run `migrate` twice and then `logins` twice. Every login connects and holds only its group's permissions, and the second run changes no password.

### Tests for User Story 2 (write first, see them fail)

- [ ] T007 [P] [US2] Unit tests in `tests/unit/storage/test_logins.py`:
  - argument parsing: `--service-host` is required, the port defaults to 5432, and `--reset` takes names that must be in the login table, else exit 2;
  - connection-string building, with the password URL-quoted;
  - the login table matches the contract's eight rows exactly;
  - output never contains the admin URL's password;
  - exit codes 0, 2 and 3.
- [ ] T008 [P] [US2] Integration tests in `tests/integration/storage/test_logins.py`, against `TEST_DATABASE_URL`, after migrations:
  - every login is created and can connect;
  - each is a member of exactly its one group role and is `NOSUPERUSER NOCREATEDB NOCREATEROLE`;
  - representative grants hold: `ta_owner_read_login` can `SELECT` from `decisions`, `risk_verdicts`, `reports` and `orchestrator_runs`, and cannot `INSERT` into `decisions` or `UPDATE` `system_state`; `ta_owner_control_login` can update only `trading_paused`; `ta_risk_gate_login` cannot read `orders`;
  - a second run leaves every password unchanged (compare `rolpassword` from `pg_authid`);
  - `--reset NAME` changes only that login;
  - with a group role dropped, the command exits 2 and creates nothing.

  Use the suite's existing role and database helpers in `tests/integration/helpers.py`, and drop the created logins in teardown.

### Implementation for User Story 2

- [ ] T009 [US2] Implement `src/trading_agent/storage/logins.py` with a `python -m trading_agent.storage.logins` entry point, following [contracts/logins-command.md](contracts/logins-command.md) exactly:
  - the eight-login table;
  - `secrets.token_urlsafe(32)` passwords;
  - `CREATE ROLE … LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD … IN ROLE …`, built with `psycopg.sql` (no string formatting of identifiers or literals);
  - a group-role precheck first, then everything in one transaction;
  - "exists, unchanged" for existing logins, and `--reset` the only way to change a password;
  - each new string printed once to stdout and nothing written to disk;
  - `ADMIN_DATABASE_URL` read with `require_env`, and exit codes 0, 2 and 3.

  Mirror `storage/migrate.py`'s style. Keep the module under 600 lines and inside the `storage` layer (no upward import). Make T007 and T008 pass.
- [ ] T010 [US2] Mutation-check T007 and T008. For example: drop `INHERIT`, change a group, let an existing login be re-passworded, print the admin URL. Confirm the matching tests fail each time, and restore by editing. Record which mutations were tried in the commit message.

**Checkpoint**: US2 is complete and independently testable.

---

## Phase 4: User Story 1 – The system runs unattended, and no order can reach the broker (P1) 🎯 MVP

**Goal**: the Railway project definition for the observe-only layout (FR-001, FR-002, FR-005, FR-007, FR-007a, FR-008, FR-009; contract [service-layout.md](contracts/service-layout.md)).

**Independent Test**: the guard test passes, and `railway config plan` lists exactly `postgres`, `orchestrator`, `risk-gate` and `reference-data`, with no `execution` and hidden values.

### Tests for User Story 1 (write first, see it fail)

- [ ] T011 [P] [US1] Guard test in `tests/unit/deploy/test_observe_only.py` (with `tests/unit/deploy/__init__.py`), reading `.railway/railway.ts` as text (research R11). It fails if the file:
  - declares a service named `execution`;
  - contains any of `ALPACA_`, `EXECUTION_` or `ADMIN_DATABASE_URL`;
  - contains `.env.DATABASE_URL`, `.env.PG` or `.env.DATABASE_PUBLIC_URL` (database-owned variables);
  - gives a variable anything other than `preserve()`;
  - has a service whose variable names differ from the contract's set for that service;
  - lacks a `release/prod` branch on every GitHub source.

  It also checks that every variable name the file declares appears in `.env.example`, and that the orchestrator's agent variables match `config/schedule.yaml`'s `env` lists for the enabled agents.

### Implementation for User Story 1

- [ ] T012 [US1] Write `.railway/package.json`: `"private": true` and the `railway` npm package as its only dependency, pinned to the exact version `npm view railway version` reports at implementation time. Generate and commit `.railway/package-lock.json` by running `npm install` in `.railway/`. Don't commit `node_modules`.
- [ ] T013 [US1] Write `.railway/railway.ts` per [contracts/service-layout.md](contracts/service-layout.md):
  - `postgres("postgres")`;
  - `orchestrator`, `risk-gate` and `reference-data`, each `source: github("itsbryanlai/trading-agent", { branch: "release/prod" })`, with its contract start command and every variable `preserve()`;
  - one replica each;
  - project name equal to the existing Railway project's name, which the owner confirms. Leave a `TODO(owner)` comment if it's unknown, and don't invent one.

  Add a header comment pointing to ADR 0021 and the guard test. Make T011 pass.
- [ ] T014 [P] [US1] Write `.railway/README.md`, a few lines: what the file is, that values are never written into it, `railway config plan` before `railway config apply`, and a link to `docs/operations/deployment.md`.
- [ ] T015 [US1] Regroup `.env.example` by Railway service (`orchestrator`, `risk-gate`, `reference-data`, then "owner's shell only": `ADMIN_DATABASE_URL`, `TEST_DATABASE_URL`, then "Execution — switch-on only"), keeping every existing name and comment. Note that each `*_DATABASE_URL` comes from the login command's output and uses the database's private host. Keep the guard test passing.
- [ ] T016 [US1] Mutation-check T011: add an `execution` service, add `ALPACA_API_KEY_ID` to `risk-gate`, set a literal value, reference `db.env.DATABASE_URL`, or remove the branch. Confirm each fails, then restore by editing.

**Checkpoint**: the MVP definition is complete. With US2, the owner can deploy observe-only.

---

## Phase 5: User Story 3 – The owner can tell the system is healthy (P2)

**Goal**: FR-006, plus observation without a dashboard (FR-004b).

**Independent Test**: each service refuses to start, naming its missing variable, and the observation queries run as `ta_owner_read_login`.

- [ ] T017 [P] [US3] Audit the existing unit tests for `python -m trading_agent.orchestrator`, `.risk` and `.reference`. Each must have a test that an unset required variable exits 2 with the variable's name in the log and no value printed. Add any missing case to the existing test file for that entry point (`tests/unit/orchestrator/`, `tests/unit/risk/`, `tests/unit/reference/`). If none is missing, record that in the T017 commit or the tasks note.
- [ ] T018 [P] [US3] Write `docs/operations/observe-queries.sql`. These are read-only queries for `ta_owner_read_login`:
  - today's reports;
  - today's decisions with their cited reports;
  - each decision's verdict and reasons;
  - decisions with no verdict yet;
  - the orchestrator's run records for today, with their outcomes and skips;
  - today's `instrument_reference` count;
  - a check that `orders` and `execution_refusals` are empty.

  Every query must use only objects the role-grants matrix gives `ta_dashboard`.
- [ ] T019 [US3] Integration test in `tests/integration/storage/test_observe_queries.py`: run every statement in `docs/operations/observe-queries.sql` as `ta_dashboard` against a migrated database, and assert that none raises. Mutation-check it by adding a query against an object `ta_dashboard` can't read, and confirm it fails.

**Checkpoint**: US3 is complete.

---

## Phase 6: User Story 4 – Trading is switched on later, as one deliberate step (P2)

**Goal**: FR-010 to FR-013, with the procedures documented and the safety assumption proven.

**Independent Test**: an integration test proves that an approval from earlier in the day lapses rather than becoming an order after the close. The runbook's switch-on and switch-off steps are complete.

- [ ] T020 [P] [US4] Check the Execution tests (`tests/unit/execution/`, `tests/integration/execution/`) for a case where an approved verdict made during a trading day is processed after that day's close, and the result is refused with `approval_expired` and no broker call. If it's missing, add it to the existing Execution integration test file, with a fake broker and a fixed clock. This proves research R7. Don't change Execution code.
- [ ] T021 [US4] Write `docs/operations/deployment.md`, the owner runbook, with these sections:
  1. **What you need**: accounts and keys by name only, and the Railway CLI and Node for the plan/apply step.
  2. **First deploy**: create the Railway project's Postgres, then run `migrate` from your machine against its public address. Run `logins --service-host <private host>` and store each printed string in a password manager, including Execution's and the owner logins'. Create `release/prod` from `main`. Run `railway config plan`, then `railway config apply`, then paste each service's values in Railway.
  3. **Release checklist**: migrate if the release adds a migration, then open a PR from `main` into `release/prod`, CI green, merge, then `railway config apply` if `.railway/` changed.
  4. **Observing**: connect as `ta_owner_read_login` and use `observe-queries.sql`.
  5. **Switching trading on**: the reviewed change that adds the `execution` service and updates the guard test. Apply only after the close and before the next open (research R7). Set its variables, with `ALPACA_BASE_URL` empty or the exact paper address. Confirm in the logs that it started on the paper account.
  6. **Switching trading off**: with no open positions, remove the service and apply. With open positions, set `trading_paused` as `ta_owner_control_login` and leave Execution running (research R8). Give the exact one-line `UPDATE`.
  7. **If something breaks**: refusals by exit code, and where the logs and run records are.

  Use variable names only. No value, no password, no example secret.

**Checkpoint**: every story is complete.

---

## Phase 7: Polish and cross-cutting

- [ ] T022 Run the full offline suite, the integration suite and `PYTHONPATH=src scripts/lint.sh`, and record the counts in the commit or PR notes.
- [ ] T023 Rehearse [quickstart.md](quickstart.md) section 3 against a local `postgres:<Railway's major version>` container on port 5434. Use the major version shown in Railway's Postgres template; if unknown, use the newest major and note it. Fix anything it shows up.
- [ ] T024 Update `specs/010-observe-only-deployment/quickstart.md` and `.railway/README.md` with anything T023 changed. Then run `/speckit-converge` (Sonnet subagent) and an adversarial review on the session's model, before the PR.

---

## Dependencies and execution order

- **Phase 1** blocks everything (Constitution V). T001 comes before T002 and T003.
- **Phase 2** blocks Phases 3 to 6.
- **US2 (Phase 3)** has no story dependency. US1 (Phase 4) can be written in parallel, but it can't be deployed without US2's logins.
- **US1 (Phase 4)**: T011 before T013. T012 before T013, because `plan` needs the package. T015 before T016.
- **US3 (Phase 5)**: T018 before T019. T017 is independent.
- **US4 (Phase 6)**: T020 is independent. T021 needs T009, T013 and T018 (it names their commands and files).
- **Phase 7** comes after everything.

## Parallel opportunities

- T004, T005 and T006.
- T007 and T008.
- T011, and T014 once T013 exists.
- T017, T018 and T020 (different files).

## Implementation strategy

1. **MVP**: Phases 1 to 4, then deploy observe-only. This already meets SC-001 to SC-005.
2. **Then**: US3 for observability, and US4 for the documented switch-on and switch-off plus the expiry proof. These must land before trading is ever switched on.
3. **Subagent phases (`CLAUDE.local.md`)**:
   - Phase 1 and 2 together, then Phase 3, then Phase 4, then Phases 5 and 6, then Phase 7.
   - Line-by-line review of T009 (credentials), T013 and T011 (the observe-only boundary), and T020 (order expiry).
