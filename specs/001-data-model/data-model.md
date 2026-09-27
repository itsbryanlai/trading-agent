# Data Model: Shared Data Model

Concrete schema for `specs/001-data-model/spec.md`. Decisions behind each choice are in
[research.md](research.md) (cited as R-numbers). Who may read/write each object is in
[contracts/role-grants.md](contracts/role-grants.md); the two computed views are in
[contracts/views.md](contracts/views.md).

Postgres 16. All timestamps `timestamptz`. Enumerations are `text` + `CHECK` (R13).

## Causal chain

```
reports ──< decision_reports >── decisions ──1:1── risk_verdicts ──1:0..1── orders
                                                                              │
                                        account_snapshots   positions  ◄──────┘ (fills)
journal (daily, reads all of the above)          system_state (singleton control row)
```

The links between these tables are the feature (SC-001). Do not collapse them.

## `reports`

One analyst-agent run. Insert-only (R5); no stored status (R6).

| Field | Type | Constraints |
|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` |
| `agent` | text | NOT NULL, `IN ('research', 'opportunistic_identifier')` |
| `generated_at` | timestamptz | NOT NULL, default `now()` |
| `symbol` | text | NULL only when `direction = 'no_action'` |
| `direction` | text | NOT NULL, `IN ('buy', 'sell', 'hold', 'no_action')` |
| `conviction` | smallint | NULL when `no_action`, else NOT NULL `BETWEEN 1 AND 5` |
| `suggested_size_pct` | numeric(6,3) | NULL when `no_action`, else `> 0 AND <= 100` |
| `sources` | jsonb | NOT NULL, default `'[]'`; must be a JSON array; non-empty unless `no_action` |
| `rationale_md` | text | NOT NULL |
| `expires_at` | timestamptz | NOT NULL, `> generated_at` (R10) |

Validation rules:
- `CHECK ((direction = 'no_action') = (symbol IS NULL))` — a `no_action` row names no symbol; every
  other row names one.
- `CHECK (jsonb_typeof(sources) = 'array')`; `CHECK (direction = 'no_action' OR
  jsonb_array_length(sources) > 0)` — a thesis without a citation is not a valid report
  (research-agent.md). Element shape (`title`, `url`, `publisher`, `published_at`) is validated
  by the writing agent, not the database.
- Row-level security: an analyst role may insert only rows whose `agent` is its own (R5).

Indexes: `(agent, generated_at DESC)`; `(symbol, expires_at)` for "open reports on symbol X".

**Derived status** (view `reports_with_status`, R6): `expired` if `expires_at <= now()`, else
`consumed` if any `decision_reports.report_id` matches, else `open`. There is no `rejected` state
(spec Clarifications).

## `decisions`

One Portfolio Manager decision. Insert-only.

| Field | Type | Constraints |
|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` |
| `generated_at` | timestamptz | NOT NULL, default `now()` |
| `symbol` | text | NOT NULL |
| `direction` | text | NOT NULL, `IN ('buy', 'sell', 'hold')` |
| `size_pct` | numeric(6,3) | NOT NULL, `>= 0 AND <= 100` |
| `reasoning_md` | text | NOT NULL |
| `quote_at_decision` | numeric(14,4) | NOT NULL, `> 0` |

Index: `(generated_at DESC)`, `(symbol, generated_at DESC)`.

## `decision_reports`

Which report(s) a decision drew on (R7). Replaces `decisions.report_ids uuid[]`.

| Field | Type | Constraints |
|---|---|---|
| `decision_id` | uuid | NOT NULL, FK → `decisions(id)` |
| `report_id` | uuid | NOT NULL, FK → `reports(id)` |

PK `(decision_id, report_id)`. Index on `report_id` (drives `consumed` status). Every decision is
expected to have at least one row (the PM originates no ideas of its own); the database can't
express "at least one child," so the PM writes both in one transaction and the Portfolio Manager
feature's tests check it.

## `risk_verdicts`

One Risk Gate evaluation. Insert-only.

| Field | Type | Constraints |
|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` |
| `decision_id` | uuid | NOT NULL, FK → `decisions(id)`, **UNIQUE** (one verdict per decision, R12) |
| `evaluated_at` | timestamptz | NOT NULL, default `now()` |
| `verdict` | text | NOT NULL, `IN ('approved', 'rejected')` |
| `rejection_rule` | text | NOT NULL iff `verdict = 'rejected'` |
| `approved_order` | jsonb | NOT NULL iff `verdict = 'approved'`; object with `symbol`, `side`, `qty`, `limit_price`, `time_in_force` |

Also `UNIQUE (id, verdict)` — target of the composite foreign key from `orders` (R12).

## `orders`

One broker submission. Written and updated by Execution only.

| Field | Type | Constraints |
|---|---|---|
| `id` | text | PK; `{trading_day}-{symbol}-{side}`; also the broker `client_order_id` (R11) |
| `risk_verdict_id` | uuid | NOT NULL |
| `verdict` | text | NOT NULL, default `'approved'`, `CHECK (verdict = 'approved')` |
| `submitted_at` | timestamptz | NOT NULL, default `now()` |
| `broker_order_id` | text | NULL until the broker acknowledges; UNIQUE when present |
| `status` | text | NOT NULL, `IN ('submitted', 'filled', 'partially_filled', 'rejected', 'canceled')` |
| `fill_price` | numeric(14,4) | NULL until a fill |
| `fill_qty` | numeric(14,4) | NULL until a fill; `>= 0` |
| `updated_at` | timestamptz | NOT NULL, default `now()` |

FK `(risk_verdict_id, verdict) → risk_verdicts(id, verdict)`: an order can only reference an
*approved* verdict (R12). `UNIQUE (risk_verdict_id)`: one order per verdict.

State transitions (written by Execution as the broker reports them):
`submitted → partially_filled → filled`, `submitted → filled`, `submitted → rejected`,
`submitted | partially_filled → canceled`. Not enforced by the database; enforced in the Execution
feature.

## `positions`

Current holdings. Written by Execution only, from confirmed fills.

| Field | Type | Constraints |
|---|---|---|
| `symbol` | text | PK |
| `qty` | numeric(14,4) | NOT NULL, `> 0` (a closed position's row is deleted) |
| `avg_entry_price` | numeric(14,4) | NOT NULL, `> 0` |
| `updated_at` | timestamptz | NOT NULL, default `now()` |

Long-only: no short positions exist in this design, hence `qty > 0`.

## `account_snapshots`

Broker account state (R8). Insert-only, written by Execution.

| Field | Type | Constraints |
|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` |
| `taken_at` | timestamptz | NOT NULL, default `now()` |
| `equity` | numeric(16,2) | NOT NULL, `>= 0` |
| `cash` | numeric(16,2) | NOT NULL |
| `buying_power` | numeric(16,2) | NOT NULL, `>= 0` |

Index `(taken_at DESC)` — readers want the latest.

## `journal`

One row per trading day. Written by the journal role; never readable by the Risk Gate or Execution
(FR-012, enforced by grants).

| Field | Type | Constraints |
|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` |
| `trading_day` | date | NOT NULL, **UNIQUE** |
| `equity_open` | numeric(16,2) | NOT NULL |
| `equity_close` | numeric(16,2) | NOT NULL |
| `summary_md` | text | NOT NULL |
| `per_agent_attribution` | jsonb | NOT NULL, `jsonb_typeof = 'object'`; keyed by agent name |
| `written_at` | timestamptz | NOT NULL, default `now()` |

`UNIQUE (trading_day)` lets a failed journal run be re-run with upsert rather than duplicated.

## `system_state`

A single control row (R9). Seeded by the migration with `trading_paused = false`, everything else
NULL.

| Field | Type | Constraints | Writer |
|---|---|---|---|
| `id` | boolean | PK, default `true`, `CHECK (id)` — at most one row | — |
| `trading_paused` | boolean | NOT NULL, default `false` | dashboard control |
| `halt_triggered_on` | date | NULL | Risk Gate |
| `baseline_trading_day` | date | NULL | Risk Gate |
| `daily_starting_equity` | numeric(16,2) | NULL, `> 0` | Risk Gate |
| `updated_at` | timestamptz | NOT NULL, default `now()` | either |

Readers use the view `system_state_effective` (contracts/views.md), which exposes
`daily_loss_halt_active` = `halt_triggered_on` equals the current trading date, and exposes the
baseline only when `baseline_trading_day` is today. Yesterday's halt reads as inactive with no
write (FR-015).

## `schema_migrations`

Runner bookkeeping (R1): `version text PK`, `applied_at timestamptz NOT NULL DEFAULT now()`. Owned
and written only by the admin role; no component is granted access.

## Deletion policy

No component is granted `DELETE` on any table except Execution on `positions` (closing a
position). History is never deleted (`docs/policy/agent-management.md`: an agent's rows outlive the
agent). Foreign keys use the default `NO ACTION`, so a referenced report, decision, or verdict
cannot be removed out from under its dependents even by an administrator by accident.
