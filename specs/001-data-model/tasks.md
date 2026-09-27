---

description: "Task list for the shared data model (feature 001)"
---

# Tasks: Shared Data Model

**Input**: Design documents from `/specs/001-data-model/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/role-grants.md,
contracts/views.md, quickstart.md

**Tests**: Included. The spec's Independent Tests, research.md R14, and the constitution's
Development Workflow ("every logic change is covered by tests") all require them. For this
feature the tests *are* the proof that permissions are enforced by the database (SC-002), so
within each story write the tests first and watch them fail before writing the migration.

**Organization**: One phase per user story. Each story ships one migration containing its tables,
constraints, views, and grants together.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4, mapping to the user stories in spec.md

## Conventions every task follows

- Group role names are exactly: `ta_research`, `ta_opportunistic_identifier`,
  `ta_portfolio_manager`, `ta_risk_gate`, `ta_execution`, `ta_journal`, `ta_orchestrator`,
  `ta_assistant`, `ta_dashboard`, `ta_dashboard_control` (contracts/role-grants.md).
- Enumerations are `text` + `CHECK (col IN (...))`; ids are `uuid DEFAULT gen_random_uuid()` except
  `orders.id` (research.md R13, R11).
- Every view is created `WITH (security_invoker = true)` (research.md R6).
- Grants are exactly the contract matrix: never `GRANT ALL`, never `TRUNCATE`, `REFERENCES`, or
  `TRIGGER` to any `ta_*` role.
- Migration SQL files contain no passwords and no `LOGIN` roles (research.md R3).
- A migration file is never edited once committed; a change is a new numbered file (R1).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton — nothing in this repo is code yet.

- [ ] T001 Create `pyproject.toml` at the repo root: project name `trading-agent`, `requires-python = ">=3.12"`, runtime dependency `psycopg[binary]>=3.2,<3.3`, optional `dev` extras `pytest>=8` and `ruff`; setuptools `src/` layout with package `trading_agent`; `[tool.pytest.ini_options]` with `testpaths = ["tests"]`, `pythonpath = ["src"]`, `addopts = "-m 'not integration'"`, and marker `integration: runs against TEST_DATABASE_URL; excluded from the default run`; `[tool.ruff]` `line-length = 100`, `target-version = "py312"`, lint `select = ["E", "F", "I", "UP", "B"]` (same as trading-bot)
- [ ] T002 [P] Create empty package and test directories: `src/trading_agent/__init__.py`, `src/trading_agent/storage/__init__.py`, `src/trading_agent/storage/migrations/` (directory), `tests/unit/storage/`, `tests/integration/storage/` (with `__init__.py` files where pytest needs them)
- [ ] T003 [P] Create `.env.example` listing `ADMIN_DATABASE_URL` (admin credential, used only by the migration step; never given to a component) and `TEST_DATABASE_URL` (a disposable database; the suite creates cluster-wide `ta_*` roles, so never the Railway database). Names and comments only, no values
- [ ] T004 [P] Create `.python-version` containing `3.12`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Connection helper, migration runner, group roles, and the test harness every story's
tests plug into.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T005 Implement `src/trading_agent/storage/db.py`: `ConfigError(Exception)`; `require_env(name) -> str` raising `ConfigError` that names the variable, never its value; `connect(url)` context manager opening a `psycopg` connection with `row_factory=dict_row`, committing on clean exit, rolling back on exception, always closing. No connection pool (same reasoning as trading-bot's `db.py`). Never log a connection string
- [ ] T006 Implement `src/trading_agent/storage/migrate.py`: `discover(directory) -> list[Migration]` returning files matching `^(\d{4})_[a-z0-9_]+\.sql$` sorted by version, raising on a duplicate version number or a `.sql` file that doesn't match the pattern; `apply_migrations(url) -> list[str]` that creates `schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())` if absent, then applies each unapplied migration in its own transaction together with its `schema_migrations` insert, and returns the versions applied; `main()` reading `ADMIN_DATABASE_URL` via `require_env`, printing each applied version, exiting non-zero on any failure; runnable as `python -m trading_agent.storage.migrate` (research.md R1, R2)
- [ ] T007 [P] Unit test `tests/unit/storage/test_migrate_discovery.py` (no database, uses `tmp_path`): files are returned in version order regardless of creation order; a duplicate version raises; a malformed `.sql` filename raises; non-`.sql` files are ignored
- [ ] T008 Write `src/trading_agent/storage/migrations/0001_roles.sql`: for each of the 10 group roles, create it `NOLOGIN` only if absent (a `DO` block checking `pg_roles`, since roles are cluster-wide and the migration must survive a database that already has them); `REVOKE CREATE ON SCHEMA public FROM PUBLIC`; `GRANT USAGE ON SCHEMA public` to each of the 10 roles; `REVOKE ALL ON schema_migrations FROM PUBLIC`
- [ ] T009 Write `tests/integration/conftest.py`: mark everything under it `integration`; a session fixture that skips the whole suite with a message naming `TEST_DATABASE_URL` when it's unset, and naming the error when the server is unreachable; creates a fresh database `ta_test_<random hex>` on that server, runs `apply_migrations` against it, yields its URL, and drops it at session end; a `conn` fixture yielding a connection to it whose work is always rolled back; an `attempt(conn, role, sql, params=None) -> "allowed" | "denied"` helper that opens a `SAVEPOINT`, runs `SET LOCAL ROLE <role>`, executes the statement, then `ROLLBACK TO SAVEPOINT` (which also undoes the role switch), returning `"denied"` on SQLSTATE `42501` (covers both missing grants and RLS violations) and re-raising any other error
- [ ] T010 Write the grants-test machinery: `tests/integration/storage/grants_matrix.py` exporting `ROLES` (the 10 names), `OPS = ("S", "I", "U", "D")`, and `GRANTS: dict[str, dict[str, set[str]]]` (object → role → ops, where column-level updates are written `"U:col"`) — start it with no objects, each story adds its own; `tests/integration/storage/factories.py` exporting a registry `FACTORIES: dict[str, Factory]` where a `Factory` knows how to seed prerequisite rows as the admin connection and produce a valid SELECT / INSERT / UPDATE / DELETE statement for its object — also start empty
- [ ] T011 Write `tests/integration/storage/test_grants.py`, parametrized over every `(object, role, op)` from `grants_matrix` and `factories`: assert `attempt(...)` is `"allowed"` exactly when the op is in `GRANTS[object][role]`, else `"denied"`. Plus one catalog test that reads privileges straight from the system catalogs — `aclexplode(pg_class.relacl)` for table/view privileges and `aclexplode(pg_attribute.attacl)` for column privileges, filtered to grantees whose `pg_roles.rolname LIKE 'ta\_%'` — **not** from `information_schema` views, whose visibility depends on the querying role's memberships and could return nothing. Map catalog privilege types to matrix ops (`SELECT`→S, `INSERT`→I, `UPDATE`→U or `U:col`, `DELETE`→D; anything else, e.g. `TRUNCATE`/`REFERENCES`/`TRIGGER`, is always a failure) and assert the resulting set **equals** the set derived from `GRANTS` in both directions: nothing granted that the contract doesn't list, nothing the contract lists left ungranted. Before the equality check, assert the catalog query returned at least one row once any story's objects exist, so an empty result can never pass silently (contracts/role-grants.md: "Adding a grant in a migration without adding it here fails the suite, and vice versa")
- [ ] T012 [P] Write `tests/integration/storage/test_roles.py`: all 10 roles exist and have `rolcanlogin = false`; none has `CREATE` on schema `public`; none has any privilege on `schema_migrations`
- [ ] T013 [P] Write `tests/integration/storage/test_migrate.py`: against a second fresh database, `apply_migrations` returns every discovered version; a second call returns `[]`; `schema_migrations` holds exactly one row per migration file

**Checkpoint**: `python -m pytest tests/ -q` passes offline; the integration suite passes with only
`0001_roles.sql` applied (grants test has no objects yet).

---

## Phase 3: User Story 1 - Analyst reports, each writer confined to its own rows (Priority: P1) 🎯 MVP

**Goal**: Research and the Opportunistic Identifier can each record reports — including "found
nothing" runs — and neither can write the other's rows.

**Independent Test**: As `ta_research`, insert a research report; as `ta_opportunistic_identifier`,
insert one of its own; each is denied when inserting the other's `agent` value; a `no_action` row
persists with no symbol.

### Tests for User Story 1 ⚠️ write first, confirm they fail

- [ ] T014 [P] [US1] Add `reports` to `tests/integration/storage/grants_matrix.py` exactly per contracts/role-grants.md: `ta_research` {S, I}, `ta_opportunistic_identifier` {S, I}, `ta_portfolio_manager` {S}, `ta_journal` {S}, `ta_assistant` {S}, `ta_dashboard` {S}, all others none. Add a `reports` factory to `tests/integration/storage/factories.py` whose INSERT uses the attempting role's own `agent` value when that role is an analyst (so the grants test checks the grant, and test_reports.py checks RLS)
- [ ] T015 [P] [US1] Write `tests/integration/storage/test_reports.py`: (a) `ta_research` inserting `agent = 'opportunistic_identifier'` is denied, and vice versa (US1 scenario 2); (b) a `no_action` row with `symbol`, `conviction`, `suggested_size_pct` NULL and `sources = '[]'` persists (scenario 3); (c) each CHECK rejects with SQLSTATE `23514`: `no_action` with a symbol, `buy` with NULL symbol, `conviction` 0 and 6, `buy` with empty `sources`, `sources` that is a JSON object not an array, `expires_at <= generated_at`, `agent = 'other'`, `direction = 'short'`; (d) a row with `expires_at` in the past is excluded by `WHERE expires_at > now()` with no UPDATE issued (scenario 4); (e) `reports` has no `status` column (spec Clarifications)

### Implementation for User Story 1

- [ ] T016 [US1] Write `src/trading_agent/storage/migrations/0002_reports.sql` per data-model.md `reports`: `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`; `agent text NOT NULL CHECK (agent IN ('research', 'opportunistic_identifier'))`; `generated_at timestamptz NOT NULL DEFAULT now()`; `symbol text`; `direction text NOT NULL CHECK (direction IN ('buy', 'sell', 'hold', 'no_action'))`; `conviction smallint`; `suggested_size_pct numeric(6,3)`; `sources jsonb NOT NULL DEFAULT '[]'`; `rationale_md text NOT NULL`; `expires_at timestamptz NOT NULL`; table CHECKs: `(direction = 'no_action') = (symbol IS NULL)`, `jsonb_typeof(sources) = 'array'`, `direction = 'no_action' OR jsonb_array_length(sources) > 0`, `expires_at > generated_at`, conviction `NULL` when `no_action` else `BETWEEN 1 AND 5`, `suggested_size_pct` `NULL` when `no_action` else `> 0 AND <= 100`; indexes `(agent, generated_at DESC)` and `(symbol, expires_at)`; `ENABLE ROW LEVEL SECURITY`; policies `INSERT TO ta_research WITH CHECK (agent = 'research')`, `INSERT TO ta_opportunistic_identifier WITH CHECK (agent = 'opportunistic_identifier')`, `SELECT USING (true)` to every role granted S; `GRANT SELECT` and `INSERT` exactly per the matrix. No `status` column. No UPDATE/DELETE grant to anyone
- [ ] T017 [US1] Run `python -m pytest tests/integration -m integration`; confirm T014/T015 now pass and the catalog test in `test_grants.py` finds no privilege on `reports` outside the matrix

**Checkpoint**: US1 is independently deliverable — analysts can persist reports and the database
enforces who wrote what.

---

## Phase 4: User Story 2 - A decision traceable end-to-end to the order it produced (Priority: P1)

**Goal**: Decision → verdict → order is a chain of database-enforced references back to the
report(s) the decision drew on; an order for an unapproved verdict is impossible.

**Independent Test**: Insert a decision citing two reports, a verdict for it, and an order for that
verdict; join order → verdict → decision → `decision_reports` → reports with no gaps; only the
owning role may write each link.

### Tests for User Story 2 ⚠️ write first, confirm they fail

- [ ] T018 [P] [US2] Add to `grants_matrix.py` and `factories.py`, exactly per the contract: `decisions` (`ta_portfolio_manager` {S, I}; `ta_risk_gate`, `ta_journal`, `ta_assistant`, `ta_dashboard` {S}); `decision_reports` (`ta_portfolio_manager` {S, I}; `ta_journal`, `ta_assistant`, `ta_dashboard` {S}); `risk_verdicts` (`ta_risk_gate` {S, I}; `ta_execution`, `ta_journal`, `ta_assistant`, `ta_dashboard` {S}); `orders` (`ta_execution` {S, I, U}; `ta_journal`, `ta_assistant`, `ta_dashboard` {S}); `reports_with_status` (`ta_portfolio_manager`, `ta_journal`, `ta_assistant`, `ta_dashboard` {S}). Factories seed their prerequisite rows (report → decision → approved verdict) as admin
- [ ] T019 [P] [US2] Write `tests/integration/storage/test_decision_chain.py`: (a) a decision citing one research and one opportunistic_identifier report stores both `decision_reports` rows (scenario 1); (b) the full join order → risk_verdict → decision → decision_reports → reports returns the original reports (SC-001); (c) a second verdict for the same decision fails `23505` (scenario 2, Edge Cases); (d) an order referencing a `rejected` verdict fails `23503`, as does one referencing a nonexistent verdict (R12); (e) a second order for the same verdict fails `23505`; (f) a second order with the same `id` `2026-09-28-AAPL-buy` fails `23505` (scenario 4); (g) CHECKs fail `23514`: rejected verdict with NULL `rejection_rule`, approved verdict with NULL `approved_order`, `approved_order` that isn't a JSON object, `orders.verdict = 'rejected'`, decision `direction = 'no_action'`, `size_pct` 101, `quote_at_decision` 0; (h) `decision_reports` pointing at a nonexistent report fails `23503`
- [ ] T020 [P] [US2] Write `tests/integration/storage/test_report_status_view.py` against `reports_with_status` (contracts/views.md): future-expiry uncited → `open`; past-expiry → `expired`; future-expiry cited by a decision → `consumed`; past-expiry *and* cited → `expired` (expiry wins); no row is ever `rejected`; computing status issued no write to `reports`; as `ta_research`, selecting the view is denied (it can't read `decision_reports`)

### Implementation for User Story 2

- [ ] T021 [US2] Write `src/trading_agent/storage/migrations/0003_decision_chain.sql` per data-model.md: `decisions` (`id uuid PK DEFAULT gen_random_uuid()`, `generated_at timestamptz NOT NULL DEFAULT now()`, `symbol text NOT NULL`, `direction text NOT NULL CHECK (direction IN ('buy', 'sell', 'hold'))`, `size_pct numeric(6,3) NOT NULL CHECK (size_pct >= 0 AND size_pct <= 100)`, `reasoning_md text NOT NULL`, `quote_at_decision numeric(14,4) NOT NULL CHECK (quote_at_decision > 0)`; indexes `(generated_at DESC)`, `(symbol, generated_at DESC)`); `decision_reports` (`decision_id uuid NOT NULL REFERENCES decisions(id)`, `report_id uuid NOT NULL REFERENCES reports(id)`, `PRIMARY KEY (decision_id, report_id)`, index on `report_id`); `risk_verdicts` (`id uuid PK`, `decision_id uuid NOT NULL UNIQUE REFERENCES decisions(id)`, `evaluated_at timestamptz NOT NULL DEFAULT now()`, `verdict text NOT NULL CHECK (verdict IN ('approved', 'rejected'))`, `rejection_rule text`, `approved_order jsonb`, CHECK `(verdict = 'rejected') = (rejection_rule IS NOT NULL)`, CHECK `(verdict = 'approved') = (approved_order IS NOT NULL)`, CHECK `approved_order IS NULL OR jsonb_typeof(approved_order) = 'object'`, `UNIQUE (id, verdict)`); `orders` (`id text PRIMARY KEY`, `risk_verdict_id uuid NOT NULL UNIQUE`, `verdict text NOT NULL DEFAULT 'approved' CHECK (verdict = 'approved')`, `FOREIGN KEY (risk_verdict_id, verdict) REFERENCES risk_verdicts (id, verdict)`, `submitted_at timestamptz NOT NULL DEFAULT now()`, `broker_order_id text UNIQUE`, `status text NOT NULL CHECK (status IN ('submitted', 'filled', 'partially_filled', 'rejected', 'canceled'))`, `fill_price numeric(14,4)`, `fill_qty numeric(14,4) CHECK (fill_qty >= 0)`, `updated_at timestamptz NOT NULL DEFAULT now()`); view `reports_with_status WITH (security_invoker = true)` = every `reports` column plus `status` computed as `'expired'` when `expires_at <= now()`, else `'consumed'` when `EXISTS` a matching `decision_reports.report_id`, else `'open'`; grants exactly per the matrix, all foreign keys default `NO ACTION`
- [ ] T022 [US2] Run `python -m pytest tests/integration -m integration`; confirm T018–T020 pass and the catalog test finds nothing outside the matrix

**Checkpoint**: US1 + US2 together cover the complete trading path's audit trail.

---

## Phase 5: User Story 3 - Holdings and daily performance with per-agent attribution (Priority: P2)

**Goal**: Positions and account state are recorded by Execution alone; the journal records each
day with per-agent attribution and is unreadable by the Risk Gate and Execution.

**Independent Test**: As `ta_execution`, write a position and an account snapshot; as `ta_journal`,
write a day's entry with attribution; `ta_risk_gate` and `ta_execution` are denied `SELECT` on
`journal`.

### Tests for User Story 3 ⚠️ write first, confirm they fail

- [ ] T023 [P] [US3] Add to `grants_matrix.py` and `factories.py`, exactly per the contract: `positions` (`ta_execution` {S, I, U, D}; `ta_portfolio_manager`, `ta_risk_gate`, `ta_journal`, `ta_assistant`, `ta_dashboard` {S}); `account_snapshots` (`ta_execution` {S, I}; `ta_portfolio_manager`, `ta_risk_gate`, `ta_journal`, `ta_assistant`, `ta_dashboard` {S}); `journal` (`ta_journal` {S, I, U}; `ta_portfolio_manager`, `ta_assistant`, `ta_dashboard` {S}; **`ta_risk_gate` and `ta_execution` none**)
- [ ] T024 [P] [US3] Write `tests/integration/storage/test_holdings_and_journal.py`: (a) `ta_execution` inserts, updates, and deletes a position (scenario 1); `qty` 0 and `avg_entry_price` 0 fail `23514`; (b) `ta_execution` inserts an account snapshot; `equity` −1 and `buying_power` −1 fail `23514`; (c) `ta_journal` inserts a day's entry, then re-runs it with `INSERT ... ON CONFLICT (trading_day) DO UPDATE` successfully, leaving exactly one row for that day (scenario 2, SC-003); a plain second insert for the same `trading_day` fails `23505`; `per_agent_attribution = '[]'` fails `23514`; (d) `SELECT` on `journal` as `ta_risk_gate` and as `ta_execution` is denied (FR-012, scenario 3)

### Implementation for User Story 3

- [ ] T025 [US3] Write `src/trading_agent/storage/migrations/0004_holdings_and_journal.sql` per data-model.md: `positions` (`symbol text PRIMARY KEY`, `qty numeric(14,4) NOT NULL CHECK (qty > 0)`, `avg_entry_price numeric(14,4) NOT NULL CHECK (avg_entry_price > 0)`, `updated_at timestamptz NOT NULL DEFAULT now()`); `account_snapshots` (`id uuid PK DEFAULT gen_random_uuid()`, `taken_at timestamptz NOT NULL DEFAULT now()`, `equity numeric(16,2) NOT NULL CHECK (equity >= 0)`, `cash numeric(16,2) NOT NULL`, `buying_power numeric(16,2) NOT NULL CHECK (buying_power >= 0)`, index `(taken_at DESC)`); `journal` (`id uuid PK DEFAULT gen_random_uuid()`, `trading_day date NOT NULL UNIQUE`, `equity_open numeric(16,2) NOT NULL`, `equity_close numeric(16,2) NOT NULL`, `summary_md text NOT NULL`, `per_agent_attribution jsonb NOT NULL CHECK (jsonb_typeof(per_agent_attribution) = 'object')`, `written_at timestamptz NOT NULL DEFAULT now()`); grants exactly per the matrix — in particular no grant of any kind on `journal` to `ta_risk_gate` or `ta_execution`
- [ ] T026 [US3] Run `python -m pytest tests/integration -m integration`; confirm T023–T024 pass and the catalog test finds nothing outside the matrix

**Checkpoint**: Everything the journal needs to compute attribution now exists.

---

## Phase 6: User Story 4 - Pause, and a daily-loss halt that clears itself (Priority: P2)

**Goal**: One control row; the dashboard toggles pause, the Risk Gate records the halt and baseline,
and yesterday's halt reads inactive today with no write.

**Independent Test**: Toggle `trading_paused` as `ta_dashboard_control`; set `halt_triggered_on` to
the current trading date as `ta_risk_gate` and see the view report active; set it to the previous
day and see it report inactive; each role is denied the other's columns.

### Tests for User Story 4 ⚠️ write first, confirm they fail

- [ ] T027 [P] [US4] Add to `grants_matrix.py` and `factories.py`, exactly per the contract, using column-level `"U:col"` entries: `system_state` (`ta_risk_gate` {S, U:halt_triggered_on, U:baseline_trading_day, U:daily_starting_equity, U:updated_at}; `ta_dashboard_control` {S, U:trading_paused, U:updated_at}; `ta_orchestrator`, `ta_assistant`, `ta_dashboard` {S}); `system_state_effective` (`ta_risk_gate`, `ta_orchestrator`, `ta_assistant`, `ta_dashboard`, `ta_dashboard_control` {S}). Extend `test_grants.py` so a `"U:col"` entry is attempted as an UPDATE of that single column, and every *other* column of `system_state` is attempted and must be denied for that role
- [ ] T028 [P] [US4] Write `tests/integration/storage/test_system_state.py`: (a) a fresh database has exactly one `system_state` row with `trading_paused = false` and the other control columns NULL (scenario 1); inserting a second row fails; (b) `ta_dashboard_control` can set `trading_paused` but is denied `halt_triggered_on`; `ta_risk_gate` can set `halt_triggered_on` but is denied `trading_paused` (scenario 2); (c) with `halt_triggered_on` = the view's `current_trading_date`, `daily_loss_halt_active` is true; with it set to `current_trading_date - 1`, it is false and no write occurred between the two reads beyond the one setup UPDATE (scenario 3, FR-015, SC-005); (d) with `baseline_trading_day = current_trading_date - 1`, the view's `daily_starting_equity` is NULL; with it equal to `current_trading_date`, the stored value shows; (e) `current_trading_date` equals `(now() AT TIME ZONE 'America/New_York')::date`; (f) `daily_starting_equity = 0` fails `23514`

### Implementation for User Story 4

- [ ] T029 [US4] Write `src/trading_agent/storage/migrations/0005_system_state.sql` per data-model.md: `system_state` (`id boolean PRIMARY KEY DEFAULT true CHECK (id)`, `trading_paused boolean NOT NULL DEFAULT false`, `halt_triggered_on date`, `baseline_trading_day date`, `daily_starting_equity numeric(16,2) CHECK (daily_starting_equity > 0)`, `updated_at timestamptz NOT NULL DEFAULT now()`); seed the one row with `INSERT ... DEFAULT VALUES`; view `system_state_effective WITH (security_invoker = true)` exposing `trading_paused`, `daily_loss_halt_active` (`COALESCE(halt_triggered_on = d, false)`), `daily_starting_equity` (the stored value only when `baseline_trading_day = d`, else NULL), `current_trading_date` (`d`), and `updated_at`, where `d = (now() AT TIME ZONE 'America/New_York')::date`; column-level grants exactly per the matrix. No `INSERT` or `DELETE` grant on `system_state` to anyone
- [ ] T030 [US4] Run `python -m pytest tests/integration -m integration`; confirm T027–T028 pass and the catalog test finds nothing outside the matrix

**Checkpoint**: All four stories complete; every downstream feature (Risk Gate, Execution,
orchestrator, agents, Assistant, dashboard) has the schema and roles it needs.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T031 [P] Add a "Development" section to `README.md`: install (`pip install -e ".[dev]"`), how migrations run (a deploy step with `ADMIN_DATABASE_URL`, never on component startup), how to run the offline and integration suites, and a pointer to `specs/001-data-model/quickstart.md` for the full walkthrough and to provisioning login roles
- [ ] T032 [P] Run `ruff check src tests` and `ruff format --check src tests`; fix anything flagged
- [ ] T033 Walk through `specs/001-data-model/quickstart.md` end to end against a fresh `postgres:16` container: migrate twice (second run applies nothing), offline suite passes, integration suite passes, integration suite reports *skipped* with `TEST_DATABASE_URL` unset, and the manual spot-check output matches what the guide says
- [ ] T034 Cross-check the finished migrations against `contracts/role-grants.md` and `docs/specs/data-model.md` one last time; if implementation forced any difference, update those documents in the same commit (Constitution Principle V)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none
- **Foundational (Phase 2)**: after Setup; blocks every story
- **US1 (Phase 3)**: after Foundational
- **US2 (Phase 4)**: after US1 — `decision_reports` and `reports_with_status` reference `reports`
- **US3 (Phase 5)**: after Foundational only; no table in it references US1/US2 tables
- **US4 (Phase 6)**: after Foundational only
- **Polish (Phase 7)**: after all stories

Migrations are applied in version order, so implement in `0002 → 0003 → 0004 → 0005` order even
where stories don't depend on each other: `0004` must not be committed ahead of `0003`.

### Within Each Story

- Matrix/factory task and test task first ([P] with each other), confirm they fail
- Then the migration
- Then the run-and-confirm task

### Parallel Opportunities

- Setup: T002, T003, T004 together after T001
- Foundational: T007 alongside T008–T011; T012 and T013 together once T009 exists
- Within each story: its two test-writing tasks together (T014+T015, T018+T019+T020, T023+T024,
  T027+T028)
- Polish: T031 and T032 together

---

## Parallel Example: User Story 2

```bash
# Tests first, together:
Task: "Add decisions/decision_reports/risk_verdicts/orders/reports_with_status to grants_matrix.py and factories.py"
Task: "Write tests/integration/storage/test_decision_chain.py"
Task: "Write tests/integration/storage/test_report_status_view.py"

# Then, once they fail for the right reason:
Task: "Write src/trading_agent/storage/migrations/0003_decision_chain.sql"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational
2. Phase 3 US1
3. **Stop and validate**: analysts can persist reports and the grants test proves the database
   confines each to its own rows

### Incremental Delivery

1. Setup + Foundational → migration runner, roles, harness
2. US1 → reports
3. US2 → full decision-to-order audit trail (US1 + US2 together = the trading path)
4. US3 → holdings, account state, journal
5. US4 → pause and self-clearing halt
6. Each migration is additive; nothing earlier is edited

---

## Notes

- A failing grants test is a contract violation, not a flaky test: fix the migration or, if the
  contract is what's wrong, update `contracts/role-grants.md` and the component's spec first
- Commit after each phase checkpoint
- Never point `TEST_DATABASE_URL` at the Railway database
