# Data Model: Orchestrator

Migration `0010_orchestrator.sql`. Decisions are in [research.md](research.md), O5–O7.

## `orchestrator_runs` (new)

One row per agent run, or per skipped slot.

| Column | Type | Rule |
|---|---|---|
| `id` | uuid PK | `gen_random_uuid()` |
| `agent` | text | CHECK in (`research`, `opportunistic_identifier`, `portfolio_manager`) |
| `trading_day` | date | The ET trading day of the slot or run |
| `reason` | text | CHECK in (`scheduled`, `morning_session`, `event_driven`, `catch_up`) |
| `slot_at` | timestamptz, nullable | The scheduled time. Null for `event_driven` |
| `catch_up_of` | text, nullable | For `catch_up` only: `research_daily` or `morning_session` |
| `started_at` | timestamptz, nullable | Null for `skipped` |
| `finished_at` | timestamptz, nullable | Null while `running` |
| `outcome` | text | CHECK in (`running`, `succeeded`, `failed`, `timed_out`, `interrupted`, `skipped`) |
| `detail` | text, nullable | Exit status, launch error type, or skip reason. Never an environment value |

Constraints:
- `outcome = 'skipped'` ⇔ `started_at IS NULL`.
- `outcome = 'running'` ⇔ `finished_at IS NULL AND started_at IS NOT NULL`.
- **No duplicate slot**: a partial unique index on `(agent, trading_day, reason, slot_at)` where `reason <> 'event_driven'`, plus a unique index on `(agent, trading_day, catch_up_of)` where `reason = 'catch_up'`.
- An index on `(agent, started_at DESC)`, for the last-run lookups.

Lifecycle: a row is inserted as `running` (or directly as `skipped`), then updated once, to `succeeded`, `failed`, `timed_out` or `interrupted`. It is never deleted.

## `latest_report_time` (new view)

`SELECT max(generated_at) AS generated_at FROM reports`: one row, one column, null when there are no reports. It is owned by the migration admin and not `security_invoker`, so the `reports` row-level security policies stay unchanged.

## `system_state` (existing, read narrowed)

`ta_orchestrator` reads only the `trading_paused` column (O7).

## Grants (amends `specs/001-data-model/contracts/role-grants.md`)

| Object | ta_orchestrator | ta_assistant | ta_dashboard |
|---|---|---|---|
| `orchestrator_runs` | S, I, U(finished_at, outcome, detail) | S | S |
| `latest_report_time` (view) | S | S | S |
| `system_state` | **S(trading_paused)** (was S) | S | S |
| `system_state_effective` | **—** (was S) | S | S |

`ta_orchestrator` has no other access. The grants-matrix test covers every row, both ways.
