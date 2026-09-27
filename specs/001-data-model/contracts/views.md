# Contract: Computed Views

Two read-only views other features query instead of reimplementing the same derivation. Both are
created `WITH (security_invoker = true)` (research.md R6): the view obeys the *caller's* table
grants, so granting a view never widens what a role can see. A caller needs `SELECT` on the view
**and** on every table it reads.

## `reports_with_status`

Every column of `reports`, plus:

| Column | Type | Value |
|---|---|---|
| `status` | text | `'expired'` if `expires_at <= now()`; else `'consumed'` if a `decision_reports` row references this report; else `'open'` |

Precedence: expiry wins over consumption — a report cited by a decision and later expired reads as
`expired`. Callers wanting "was this report ever acted on" join `decision_reports` directly.

Guarantees:
- Reflects the current moment on every read; no background job has to run first (spec Edge Cases).
- Never returns `'rejected'` (spec Clarifications).

Typical consumer: the Portfolio Manager's session-start query — `WHERE status = 'open'`.

Requires: `SELECT` on `reports` and `decision_reports`. Research and the Opportunistic Identifier
are deliberately not granted it (they may not read `decision_reports`); they filter
`reports.expires_at > now()` on the base table to avoid re-emitting a still-open report.

## `system_state_effective`

One row:

| Column | Type | Value |
|---|---|---|
| `trading_paused` | boolean | stored value |
| `daily_loss_halt_active` | boolean | `halt_triggered_on = current_trading_date` (false when NULL) |
| `daily_starting_equity` | numeric | stored value when `baseline_trading_day = current_trading_date`, else NULL |
| `current_trading_date` | date | `(now() AT TIME ZONE 'America/New_York')::date` |
| `updated_at` | timestamptz | stored value |

Guarantees:
- A halt set on day D reads `true` for the rest of day D and `false` from the start of day D+1 with
  no write by anyone (FR-015, User Story 4).
- A NULL `daily_starting_equity` means "no baseline recorded yet today" — the Risk Gate feature
  must record one before it can evaluate the daily-loss line, rather than reuse yesterday's.

Typical consumers: the orchestrator (checks `trading_paused` before invoking the PM), the Risk Gate
(checks `daily_loss_halt_active`, reads the baseline), the Assistant and dashboard.

Writers never write through this view; they `UPDATE system_state` directly, restricted by column
grants (contracts/role-grants.md).
