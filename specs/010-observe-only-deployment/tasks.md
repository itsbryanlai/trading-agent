---

description: "Task list for feature 010, observe-only deployment (revised after analyze)"
---

# Tasks: Observe-Only Deployment

**Input**: Design documents from `specs/010-observe-only-deployment/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: included (`CLAUDE.md`). Mutation-check every new test: break the code, see the test fail, then restore the file by editing it (never `git checkout` or `git stash`).

**Organization**: by user story.
- US2 (database setup) comes before US1 (services), because the services need US2's logins. Both are P1.
- The orchestrator setting is foundational: US1's guard test checks it.

**Rules for every task**:
- Atomic commits in the `git log` style (`Area (feature 010): what changed (T0NN)`), staged by explicit path.
- Run Python with `PYTHONPATH=src`. After any logic change, run `PYTHONPATH=src scripts/lint.sh` and the relevant tests.
- Read no `.env` file.
- Touch no risk limit, sizing rule or order logic (FR-014). The only schedule change is `run_while_paused`.
- Never place, cancel or modify a broker order, and never call a broker or the network from tests.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story the task belongs to (US1–US4)

---

## Phase 1: Setup (decision records, before any code)

**Purpose**: Constitution V. ADR first.

- [X] T001 Write `docs/adr/0021-railway-deployment-as-code-observe-only-first.md` (status Accepted, using the template in `docs/adr/README.md`). It records:
  - Railway infrastructure as code in TypeScript `.railway/railway.ts`, replacing the deprecated `railway.json` (research R1);
  - one service per process, the orchestrator also carrying its agents' variables (R3);
  - the setup and login commands run from the owner's machine, with the public proxy closed between uses (R5, R6);
  - deploys only from `release/prod` (R10);
  - observe-only means Execution deployed, trading paused before any service starts, and a flat paper account before Execution's first start (R0, R9);
  - the new `portfolio_manager.run_while_paused` setting, which reverses spec 005's "no PM runs while paused" for observation and keeps the unknown-flag case fail-closed (R8);
  - switch-on only after the close (R7), and both switch-off forms (R8).

  Add its row to `docs/adr/README.md`.
- [X] T002 Amend the "Technology & Deployment Constraints" Railway bullet in `.specify/memory/constitution.md` to name infrastructure as code and one worker service per process, citing ADR 0021. Bump 1.1.0 → 1.1.1 (PATCH), update **Last Amended**, and add a Sync Impact Report. Leave every principle unchanged.
- [X] T003 [P] Update the "Deployment shape" section of `docs/architecture/overview.md`: the four services, Railpack, `.railway/railway.ts`, `release/prod`, and observe-only through the pause. Cite ADR 0021. Replace "Nixpacks build".
- [X] T004 [P] Update `specs/005-orchestrator/spec.md`'s pause behaviour (the US with pause scenarios, FR "(e) trading is not paused", and SC-004) with a note: "unless `portfolio_manager.run_while_paused` is true (ADR 0021); an unreadable flag still blocks". Add a matching line to `specs/005-orchestrator/contracts/orchestrator-interface.md`'s Configuration section. Change no other behaviour.

**Checkpoint**: the decisions are recorded, and code may start.

---

## Phase 2: Foundational (build files and the orchestrator setting)

- [X] T005 [P] Create `.python-version` containing `3.12` (research R2).
- [X] T006 [P] Create `requirements.txt` containing `-e .`, with a comment: editable so the config loaders that resolve `config/` from `Path(__file__).parents[3]` find the repository root (research R2). Confirm that `.venv/bin/pip install -r requirements.txt --dry-run` resolves.
- [X] T007 [P] Add `.railway/node_modules/` to `.gitignore`.
- [X] T008 Tests first, per [contracts/observe-setting.md](contracts/observe-setting.md). Extend `tests/unit/orchestrator/test_config.py` so the key is required and must be a boolean, with the error naming `portfolio_manager.run_while_paused`. Extend `tests/unit/orchestrator/test_planner_pause.py` with:
  - setting `false` and paused: blocked, with the existing tests unchanged;
  - setting `true` and paused: the morning session and event-driven runs start as on an unpaused day;
  - setting `true` and an unknown flag: blocked, and an unknown flag at the morning session records the skip and claims the slot (pins the existing behaviour, analyze N4);
  - a Hypothesis property: with `true`, the PM plan for a paused day equals the unpaused plan. Put it in `test_planner_properties.py` beside the existing properties.

  Update the shared fixtures in `tests/unit/orchestrator/support.py` so existing tests build a config with `run_while_paused=False`.
- [X] T009 Implement the setting:
  - add `run_while_paused: bool` to `PortfolioManagerConfig` and `_KEYS` in `src/trading_agent/orchestrator/config.py`, required and boolean-checked;
  - in `src/trading_agent/orchestrator/planner.py`, `_pause_block` (or its PM call sites) skips the block for a known `paused=True` when the setting is true, and still blocks on `None`;
  - in `src/trading_agent/orchestrator/__main__.py`, log one line at startup when true: `portfolio_manager runs while paused (observe-only, ADR 0021)`;
  - add `run_while_paused: true` to `config/schedule.yaml` under `portfolio_manager`, with a comment citing ADR 0021 and "set false at switch-on".

  Make T008 pass. Analysts must stay unaffected.
- [X] T010 Mutation-check T008 and T009:
  - make `None` unblock;
  - ignore the setting at the event-driven call site only (analyze N5);
  - default the key instead of requiring it;
  - invert the check.

  Each must fail a test. Restore by editing. Record the mutations in the commit message.

**Checkpoint**: an editable 3.12 build, and a PM that can run while paused only by a reviewed setting.

---

## Phase 3: User Story 2 – A fresh database is built correctly and safely (P1)

**Goal**: one owner-run command creates every login with exactly its group role's permissions (FR-004, FR-004a, FR-004b; [contracts/logins-command.md](contracts/logins-command.md)).

**Independent Test**: on a throwaway Postgres, run `migrate` twice and `logins` twice. Every login connects with only its group's permissions, and the second run changes no password. The control login can set the pause.

- [ ] T011 [P] [US2] Unit tests in `tests/unit/storage/test_logins.py`:
  - argument parsing: `--service-host` is required, the port defaults to 5432, and `--reset` must name a known login, else exit 2;
  - connection-string building, with the password URL-quoted;
  - the login table equals the contract's eight rows;
  - output never contains the admin URL's password;
  - exit codes 0, 2 and 3.
- [ ] T012 [P] [US2] Integration tests in `tests/integration/storage/test_logins.py`, against `TEST_DATABASE_URL` after migrations. Reuse `tests/integration/storage/grants_matrix.py` and the helpers in `tests/integration/helpers.py`. Check that:
  - every login is created and can connect;
  - each is a member of exactly its one group role and is `NOSUPERUSER NOCREATEDB NOCREATEROLE`;
  - `ta_owner_read_login` can `SELECT` from `decisions`, `risk_verdicts`, `reports`, `account_snapshots`, `execution_refusals` and `orchestrator_runs`, and cannot `INSERT` into `decisions` or `UPDATE` `system_state`;
  - `ta_owner_control_login` can `UPDATE system_state SET trading_paused = true, updated_at = now()` and cannot update a halt column;
  - `ta_risk_gate_login` cannot read `orders`;
  - a second run leaves every `rolpassword` unchanged;
  - `--reset NAME` changes only that login;
  - with a group role dropped, the command exits 2 and creates nothing.

  Drop the created logins in teardown.
- [ ] T013 [US2] Implement `src/trading_agent/storage/logins.py` (`python -m trading_agent.storage.logins`) per the contract exactly:
  - the eight-login table;
  - `secrets.token_urlsafe(32)` passwords;
  - `CREATE ROLE … LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD … IN ROLE …`, built only with `psycopg.sql`;
  - a group-role precheck, then one transaction;
  - "exists, unchanged" for existing logins, and `--reset` the only way to change a password;
  - strings printed once to stdout, nothing written to disk;
  - `ADMIN_DATABASE_URL` read with `require_env`, and exit codes 0, 2 and 3.

  Mirror `storage/migrate.py`'s style. Stay under 600 lines and inside `storage`. Make T011 and T012 pass.
- [ ] T014 [US2] Mutation-check T011 and T012:
  - drop `INHERIT`;
  - swap two groups;
  - re-password an existing login;
  - print the admin URL;
  - skip the precheck.

  Each must fail a test. Restore by editing, and record the mutations in the commit.

**Checkpoint**: US2 is complete.

---

## Phase 4: User Story 1 – The system runs, and no order can reach the broker (P1) 🎯 MVP

**Goal**: the Railway project definition (FR-001, FR-002, FR-005, FR-007, FR-007a, FR-008, FR-009; [contracts/service-layout.md](contracts/service-layout.md)).

**Independent Test**: the guard test passes, and `railway config plan` lists exactly `postgres`, `orchestrator`, `risk-gate`, `reference-data` and `execution`, with hidden values.

- [ ] T015 [US1] Guard test in `tests/unit/deploy/test_deployed_shape.py` (with `tests/unit/deploy/__init__.py`), reading `.railway/railway.ts` as text (research R11). It fails if:
  - any `ALPACA_` or `EXECUTION_` name appears outside the `execution` service's block;
  - `ADMIN_DATABASE_URL` appears;
  - `.env.DATABASE_URL`, `.env.DATABASE_PUBLIC_URL` or `.env.PG` appears;
  - any variable value isn't `preserve()`;
  - a service's variable names differ from the contract's set for it;
  - a GitHub source lacks `branch: "release/prod"`;
  - the set of services differs from the contract.

  It also checks that every declared name appears in `.env.example`, that the orchestrator's agent names equal `config/schedule.yaml`'s `env` lists for the enabled agents, and that `config/schedule.yaml` has `run_while_paused: true`.
- [ ] T016 [US1] **Main session, not a subagent (network).** Write `.railway/package.json`: `"private": true`, with the `railway` npm package as its only dependency, pinned to the exact version from `npm view railway version`. Run `npm install` in `.railway/` and commit `package-lock.json`. Don't commit `node_modules`.
- [ ] T017 [US1] Write `.railway/railway.ts` per the contract:
  - `postgres("postgres")` and the four services;
  - each with `source: github("itsbryanlai/trading-agent", { branch: "release/prod" })`, its start command, and every variable `preserve()`;
  - one replica each;
  - the project name from the owner. If it's unknown, leave a `TODO(owner)` comment and don't invent one.

  Add a header comment citing ADR 0021 and the guard test. Make T015 pass.
- [ ] T018 [P] [US1] Write `.railway/README.md`: what the file is, that values are never written into it, `plan` before `apply`, and a link to `docs/operations/deployment.md`.
- [ ] T019 [US1] Regroup `.env.example` by service (`orchestrator`, `risk-gate`, `reference-data`, `execution`, then "owner's shell only": `ADMIN_DATABASE_URL`, `TEST_DATABASE_URL`). Keep every existing name and comment. Note that each `*_DATABASE_URL` comes from the login command and uses the private host. Keep T015 passing.
- [ ] T020 [US1] Mutation-check T015:
  - put `ALPACA_API_KEY_ID` on `risk-gate`;
  - set a literal value;
  - reference `db.env.DATABASE_URL`;
  - drop the branch;
  - set `run_while_paused: false`;
  - add an undeclared service.

  Each must fail. Restore by editing.

**Checkpoint**: the MVP definition is complete. With US2 and the runbook, the owner can deploy observe-only.

---

## Phase 5: User Story 3 – The owner can tell the system is healthy (P2)

**Independent Test**: each service refuses to start, naming its missing variable, and every observation and post-deploy query runs as the read-only role.

- [ ] T021 [P] [US3] Audit the existing entry-point tests for `orchestrator`, `risk`, `reference` and `execution` (`tests/unit/<component>/`). Each must have a case where an unset required variable exits 2 with the name in the log and no value printed. Add any missing case to that component's existing test file. Also confirm a test covers Execution's `NotPaperTrading` refusal (exit 2) (analyze C9). Record the audit result in the commit.
- [ ] T022 [P] [US3] Write `docs/operations/observe-queries.sql`, read-only, for `ta_owner_read_login`:
  - today's reports;
  - decisions with their cited reports;
  - each decision's verdict and reasons;
  - decisions without a verdict;
  - account snapshots today;
  - Execution's refusals with reasons;
  - orchestrator run records, with outcomes and skips;
  - today's `instrument_reference` count.

  Add two blocks from research R12:
  - **"pre-open"**: paused is true, `positions` is empty;
  - **"first trading day"**: paused is true, `positions` is empty, a snapshot exists from today, at least one decision exists, every decision has a verdict, no buy verdict is approved, and every buy rejection reads `trading_paused`, `market_closed` or `decision_stale`, and `orders` is empty. Use only objects `ta_dashboard` may read.
- [ ] T023 [US3] Integration test in `tests/integration/storage/test_observe_queries.py`: run every statement in that file as `ta_dashboard` against a migrated database, and assert that none raises. Mutation-check it by adding a query on an object `ta_dashboard` can't read.

---

## Phase 6: User Story 4 – Trading is switched on later, as one deliberate step (P2)

**Independent Test**: tests prove that the gate rejects buys while paused, that Execution refuses them as a second layer, and that a day's approvals lapse after the close. The runbook's switch-on and switch-off are complete.

- [ ] T024 [P] [US4] Confirm that the existing Execution tests (`tests/unit/execution/`, `tests/integration/execution/`) cover:
  - (a) an approved buy while paused is refused `trading_paused`, with no broker submit;
  - (b) an approved buy whose trading day has closed is refused `approval_expired`, with no broker submit;
  - (c) an approved sell while paused is still placed;
  - (d) in the Risk Gate tests (`tests/unit/risk/`), a buy decision while paused (fresh quote, market open) is rejected `trading_paused` before any sizing, universe or cash rule, and a sell decision while paused is still evaluated.

  Add any missing case to the existing test files, with a fake broker and a fixed clock. Don't change Execution code.
- [ ] T025 [US4] Write `docs/operations/deployment.md`, the owner runbook, using variable names only (never a value):
  1. **What you need**: accounts and keys by name; the Railway CLI and Node for plan and apply.
  2. **First deploy, in this exact order**:
     1. create the Railway project and its Postgres;
     2. enable the database's public TCP proxy;
     3. run `migrate` from your machine;
     4. run `logins --service-host <private host>`, and store every printed string in a password manager;
     5. set the pause as `ta_owner_control_login`, giving the exact `UPDATE`, and read it back;
     6. disable the public proxy;
     7. **⚠️ REMINDER: close every position and cancel every open order in the Alpaca paper account, in Alpaca's own interface, and confirm it shows zero positions and zero open orders.** Do this before Execution can ever start, because it reconciles broker positions and would place their stop-loss exits even while paused;
     8. create `release/prod` from `main`;
     9. run `railway config plan`, check it, then `railway config apply`;
     10. paste each service's values, with `ALPACA_BASE_URL` empty or exactly the paper address;
     11. confirm in Execution's log that it started on the paper account;
     12. **before the next open**, run the "pre-open" block of `observe-queries.sql`. Paused must be true and `positions` empty, and re-confirm zero open orders in Alpaca (analyze P2). If either fails, remove `execution` from `.railway/railway.ts` and apply before the open (analyze N2, N6).
  3. **Release checklist**: proxy on, then `migrate`, then proxy off, if the release adds a migration. Then a PR from `main` into `release/prod`, CI green, merge, and `railway config apply` if `.railway/` changed.
  4. **Observing and the post-deploy check**: `observe-queries.sql` as `ta_owner_read_login`, and the first-trading-day block after day one.
  5. **Switching trading on**, in this order (analyze N3):
     1. while still paused, a reviewed change sets `run_while_paused: false` (and updates the guard test), released via `release/prod`;
     2. then, after the close and before the next open, clear the pause (exact `UPDATE`) and read it back.
  6. **Switching trading off**: pause (sells and stop-loss exits continue), or remove `execution` from `.railway/railway.ts` and apply. Removal is only allowed after the close with zero positions, zero open orders and `in_flight_orders` empty, with a query for each.
  7. **If something breaks**: exit codes 2 and 3, and where the logs and run records are.

---

## Phase 7: Polish and cross-cutting

- [ ] T026 Run the full offline suite, the integration suite and `PYTHONPATH=src scripts/lint.sh`. Record the counts.
- [ ] T027 Rehearse [quickstart.md](quickstart.md) section 3 against a local `postgres:<Railway's major>` on port 5434, starting each service from the repository root. Confirm the gate loads `config/risk.yaml` and the orchestrator logs the observe-only line. Fix and record anything it shows up.
- [ ] T028 Fold any rehearsal changes into `quickstart.md` and `.railway/README.md`. Then run `/speckit-converge` (Sonnet subagent), and an adversarial review on the session's model, before the PR.

---

## Dependencies and execution order

- **Phase 1** blocks everything. T001 comes before T002–T004.
- **Phase 2** blocks Phases 3–6. T008 before T009 before T010.
- **US2** has no story dependency. US1's T015 needs T009, which adds the setting it checks.
- **US1**: T015 before T017, and T016 before T017. T019 before T020.
- **US3**: T022 before T023.
- **US4**: T025 needs T013, T017 and T022.
- **Phase 7** comes last.

## Parallel opportunities

- T003 and T004.
- T005–T007.
- T011 and T012.
- T018 once T017 exists.
- T021, T022 and T024.

## Implementation strategy

1. **MVP**: Phases 1–4, plus T022 and T025, because the runbook and the post-deploy check are needed to deploy safely. Then deploy observe-only.
2. **Before trading is ever switched on**: the rest of US3 and US4.
3. **Subagent phases (`CLAUDE.local.md`)**:
   - Phase 1, then Phase 2, then Phase 3, then Phase 4 (T016 in the main session), then Phases 5–6, then Phase 7.
   - Line-by-line review of T009 (pause handling), T013 (credentials), T015 and T017 (the deployed shape), and T024 (Execution's refusals).
