# Research: Shared Data Model

Phase 0 decisions for `specs/001-data-model`. Each entry resolves an unknown from the plan's
Technical Context or a gap found while mapping the spec onto a real schema.

## R1. Schema evolution: forward-only numbered SQL migrations

- **Decision**: Plain SQL files `src/trading_agent/storage/migrations/NNNN_name.sql`, applied in
  order by a small runner that records each version in a `schema_migrations` table. Each file
  runs in its own transaction. No ORM, no Alembic.
- **Rationale**: `trading-bot` uses one idempotent `schema.sql` (`CREATE TABLE IF NOT EXISTS`)
  applied on every start. That silently ignores column changes to an existing table — fine for
  one feature, a trap for this project, where seven more features will extend this schema.
  Numbered files make each change explicit and ordered, at the cost of a ~40-line runner.
- **Alternatives considered**: Alembic (adds SQLAlchemy as a dependency for a project that
  otherwise uses raw `psycopg`; autogenerate is useless without ORM models). Idempotent single
  file (rejected above).

## R2. Who runs migrations: a separate admin step, never the components

- **Decision**: Migrations run with an admin credential (`ADMIN_DATABASE_URL`) as a deploy step
  (`python -m trading_agent.storage.migrate`), not on each component's startup.
- **Rationale**: In `trading-bot`, `init_schema()` runs on startup with a credential that can do
  DDL. Here every component connects with a role that, by design (Principle III), cannot create
  or alter tables. A component able to run migrations would be able to grant itself anything.
- **Alternatives considered**: Run on worker startup with the admin URL also present in the
  worker's environment — rejected, it puts a superuser credential inside the process running LLM
  agents.

## R3. Role model: NOLOGIN group roles in migrations, login roles out-of-band

- **Decision**: Migrations create one `NOLOGIN` group role per component and all `GRANT`s to those
  group roles. An operator creates one `LOGIN` role per component with a password, out-of-band,
  as a member of its group role (`CREATE ROLE ... LOGIN PASSWORD ... IN ROLE ta_research`).
- **Rationale**: Grants live in version control and are tested; passwords never touch the repo
  (Constitution: credentials never committed). Mirrors `trading-bot`'s README-documented role
  creation, but moves the grants themselves out of a README and into reviewed, tested SQL.
- **Alternatives considered**: Creating login roles with passwords inside migrations (secrets in
  repo). Keeping all grants in a README as `trading-bot` does (untested; drifts silently).

## R4. Read grants derived from each component's spec, not "broad by default"

- **Decision**: Each role gets `SELECT` only on what its `docs/specs/*.md` says it reads. Research
  and the Opportunistic Identifier read `reports` only; the orchestrator reads `system_state`
  only; the Risk Gate and Execution get no access to `journal`. The Assistant and dashboard read
  everything.
- **Rationale**: The component specs already state these limits (e.g. research-agent.md: "Never
  reads or writes `decisions`, `risk_verdicts`, `orders`, or `positions`"), and
  `docs/policy/agent-management.md` says unused grants are "dead surface, not neutral." Spec
  FR-016's "broad by default unless a table's requirements say otherwise" is satisfied: the
  component specs are where "otherwise" is said.
- **Alternatives considered**: `SELECT` on everything for everyone except `journal` — simpler
  matrix, but grants access several specs explicitly disclaim.

## R5. Per-agent row restriction on `reports`: row-level security

- **Decision**: Enable RLS on `reports`. `INSERT` policies: `ta_research` `WITH CHECK (agent =
  'research')`; `ta_opportunistic_identifier` `WITH CHECK (agent = 'opportunistic_identifier')`.
  A permissive `SELECT USING (true)` policy for every role granted `SELECT`. No `UPDATE`/`DELETE`
  grant to anyone (reports are insert-only, per the clarification that status is computed).
- **Rationale**: Table grants alone can't say "only rows where agent = X". RLS is Postgres's
  native mechanism for exactly that, and it's enforced beneath the application (FR-017).
- **Alternatives considered**: Two tables (`research_reports`, `oi_reports`) — every reader must
  union them, and a third analyst means a third table. A trigger checking `current_user` —
  works, but reimplements RLS by hand.

## R6. Report status: a `security_invoker` view, not a column

- **Decision**: No `status` column on `reports`. A view `reports_with_status` computes
  `expired` (`expires_at <= now()`), `consumed` (exists in `decision_reports`), else `open`. The
  view is created `WITH (security_invoker = true)`.
- **Rationale**: Directly implements the 2026-09-27 clarification. `security_invoker` makes the
  view obey the *caller's* privileges; a default Postgres view runs with its owner's privileges,
  which would let any role granted the view read past its own table grants.
- **Alternatives considered**: Default (owner-privilege) view — silently widens access.
  Materialized status column maintained by trigger — reintroduces a stored transition.

## R7. `decisions.report_ids` becomes a junction table

- **Decision**: Replace the `report_ids uuid[]` column from `docs/specs/data-model.md` with a
  `decision_reports (decision_id, report_id)` table, both columns foreign keys, primary key on
  the pair. Written only by `ta_portfolio_manager`, in the same transaction as its decision.
- **Rationale**: Postgres cannot enforce a foreign key on array elements. SC-001 requires every
  order to trace back to its reports "with no broken or ambiguous links"; an array can hold an id
  that doesn't exist. The behavior (a decision cites one or more reports) is unchanged; only the
  representation is.
- **Alternatives considered**: Keep the array and validate with a trigger — reimplements a
  foreign key by hand, and still can't stop a report being deleted out from under it.
- **Spec impact**: `docs/specs/data-model.md` updated alongside this plan (Principle V).

## R8. Account cash/equity needed a home: `account_snapshots`

- **Decision**: New table `account_snapshots (id, taken_at, equity, cash, buying_power)`, written
  only by `ta_execution`, read by the Portfolio Manager, Risk Gate, journal, Assistant, and
  dashboard.
- **Rationale**: Gap in the accepted spec. The PM spec reads "cash"; the Risk Gate enforces a
  cash reserve and a daily-loss line on equity; the journal records `equity_open/close`. None had
  a source. The Risk Gate is a pure function with no broker access, so account state must come
  from the database — and Execution is the only component holding the broker credential, so it
  is the only possible writer.
- **Alternatives considered**: Pass cash/equity in from each caller's own broker call — gives the
  PM, journal, and Risk Gate's caller broker credentials, violating Principles I and III.
- **Spec impact**: Added as FR-018 in `spec.md` and a table in `docs/specs/data-model.md`. Not an
  ADR: it fills a missing storage location for data flows the accepted specs already describe,
  rather than adding a new flow.

## R9. `system_state`: a typed singleton row with column-level grants, and a date-based halt

- **Decision**: One row (`id boolean PRIMARY KEY DEFAULT true CHECK (id)`) with typed columns:
  `trading_paused boolean`, `halt_triggered_on date NULL`, `baseline_trading_day date NULL`,
  `daily_starting_equity numeric NULL`. Column-level grants: `ta_dashboard_control` may `UPDATE
  (trading_paused)` only; `ta_risk_gate` may `UPDATE (halt_triggered_on, baseline_trading_day,
  daily_starting_equity)` only. A `security_invoker` view `system_state_effective` exposes
  `daily_loss_halt_active = (halt_triggered_on = current trading date)` and treats the baseline
  as unset unless `baseline_trading_day` is today.
- **Rationale**: A key-value table with mixed types needs RLS or triggers to give different keys
  different writers; column grants do it natively. Storing the *date* the halt fired rather than
  a boolean makes the halt clear itself at the next trading day with zero writes — satisfying
  FR-015 and User Story 4 ("no write from a human or agent") by the same computed-at-read pattern
  the report-status clarification chose.
- **Alternatives considered**: A stored boolean reset by the Risk Gate's first evaluation of the
  day — if the Risk Gate never runs that day, yesterday's halt appears to persist.
- **Current trading date** is `(now() AT TIME ZONE 'America/New_York')::date`. A halt can only
  be set on a trading day, and only equality with today is checked, so weekends and holidays
  need no calendar in the database.

## R10. Report expiry is computed by the writer, bounded by the database

- **Decision**: The analyst agent sets `expires_at` using the exchange calendar (in its own
  code); the database enforces only `CHECK (expires_at > generated_at)`.
- **Rationale**: "End of the trading day" needs a holiday calendar that belongs in application
  code, not SQL. The check still catches an obviously wrong value.

## R11. Deterministic order id: a text key that doubles as the broker's `client_order_id`

- **Decision**: `orders.id` is `text`, formatted `{trading_day}-{symbol}-{side}` (e.g.
  `2026-09-28-AAPL-buy`), primary key. Execution sends the same string as Alpaca's
  `client_order_id`, so the broker also rejects a duplicate.
- **Rationale**: Readable in logs and the dashboard, deterministic, and gives two independent
  duplicate guards (the primary key and the broker). Same trade-off as `trading-bot`: at most one
  order per symbol per side per day.
- **Known limitation, deferred to the Execution feature**: a stop-loss exit and a PM sell of the
  same symbol on the same day would share an id. Execution's spec must decide whether that's
  acceptable or whether the id needs an order-kind component.

## R12. "An order for an unapproved verdict" is structurally impossible

- **Decision**: `risk_verdicts` has `UNIQUE (id, verdict)`. `orders` carries `verdict text NOT
  NULL DEFAULT 'approved' CHECK (verdict = 'approved')` and a composite foreign key
  `(risk_verdict_id, verdict) REFERENCES risk_verdicts (id, verdict)`.
- **Rationale**: A plain foreign key only proves the verdict exists. The composite key proves it
  exists *and* is approved, enforced by the database (Edge Cases, FR-008).
- Also: `UNIQUE (decision_id)` on `risk_verdicts` guarantees one verdict per decision, closing
  the concurrent-evaluation edge case.

## R13. Enumerations as `text` + `CHECK`, ids as `uuid`

- **Decision**: Enumerated columns are `text` with `CHECK (col IN (...))`, following
  `trading-bot`. Ids (except `orders.id`, R11) are `uuid DEFAULT gen_random_uuid()`.
- **Rationale**: Postgres `ENUM` types resist change (`ALTER TYPE ... ADD VALUE` can't run in a
  transaction block before PG12 and values can't be removed). `gen_random_uuid()` is built in
  from PG13.

## R14. Testing: real Postgres, every role actually assumed

- **Decision**: Integration tests run against a disposable Postgres 16 (local Docker; CI later),
  apply all migrations to a fresh database, then for each row of the grants matrix
  (`contracts/role-grants.md`) `SET ROLE` to the group role inside a transaction and assert the
  write succeeds or raises `InsufficientPrivilege` / an RLS violation. Every transaction is
  rolled back. The suite skips with a clear message if `TEST_DATABASE_URL` is unset.
- **Rationale**: `trading-bot`'s integration tests all run as one privileged user, so its grants
  are never exercised. SC-002 requires forbidden writes be rejected 100% of the time, which only a
  test that actually assumes each role can show. The matrix-driven test also fails if a grant is
  added that the contract doesn't list.
- **Alternatives considered**: testcontainers-python (adds a dependency and Docker-in-test
  coupling; can be adopted later without changing the tests).

## R15. Stack versions

- Python 3.12 (matches `trading-bot`), `psycopg[binary]` 3.2, `pytest` 8. Postgres 16 — required
  for `security_invoker` views (PG15+); Railway's managed Postgres provides 16.
