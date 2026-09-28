# Data Model: Execution

Changes this feature makes to the shared knowledge base
([feature 001 data model](../001-data-model/data-model.md), [feature 002
changes](../002-risk-gate/data-model.md)), all in one forward-only migration,
`0007_execution.sql`. Decisions are cited as E-numbers from [research.md](research.md). The
permissions are in the amended [role-grants contract](../001-data-model/contracts/role-grants.md).

No new role. `ta_execution` already exists (feature 001).

## Changed table: `orders` (E3, E7, E8)

`orders` has no rows in any environment yet (nothing could write it before this feature), so every
change below is a plain `ALTER` with no backfill.

| Change | Detail |
|---|---|
| `id` format | **new** `CHECK (id ~ '^\d{4}-\d{2}-\d{2}-[A-Z][A-Z0-9.]*-(buy\|sell)-[0-9a-f]{8}$')` ([ADR 0012](../../docs/adr/0012-order-identifier-per-verdict.md)) |
| `id` tied to its verdict | **new** `CHECK (right(id, 8) = left(risk_verdict_id::text, 8))`: the suffix can't name a different verdict than the row points at |
| `status` | CHECK widened to `('submitted', 'partially_filled', 'filled', 'rejected', 'canceled', 'expired')`. `expired` is a day order the broker closed out at the end of the session, filled or not (the broker's `expired` and `done_for_day`, E7). |
| `limit_price` | **new**, `numeric(14,4)`, `> 0`: the live ask a buy was actually submitted at (≤ the verdict's ceiling), also when the row is recorded by the crash-recovery lookup. **new** `CHECK ((id ~ '-buy-[0-9a-f]{8}$') = (limit_price IS NOT NULL))`: every buy has one, no sell does, so the open-buy cost sum can't skip a row (E6). |
| `broker_reason` | **new**, `text`, nullable: the broker's stated reason when it rejects an order |
| `submitted_at` | unchanged column; now always written explicitly with the submission time rather than left to the default |
| grants | `UPDATE` for `ta_execution` narrowed from the whole row to `(broker_order_id, status, fill_qty, fill_price, broker_reason, updated_at)` (E10) |

Unchanged: `risk_verdict_id UNIQUE`, the always-`'approved'` `verdict` column and its composite
foreign key to `risk_verdicts (id, verdict)` (001 research R12). An order for a rejected verdict
stays structurally impossible.

`orders` has no symbol, side or quantity columns; those come from the verdict's `approved_order` by joining on `risk_verdict_id` (E6).

`fill_qty` and `fill_price` are the broker's cumulative filled quantity and average fill price. The
difference between two successive readings is the fill that `positions` hasn't absorbed yet (E8).

### Order states

```text
            ┌──────────► filled
submitted ──┼──► partially_filled ──┬──► filled
            │                       └──► expired | canceled   (with fill_qty > 0)
            ├──► expired | canceled                            (fill_qty = 0)
            └──► rejected
```

`filled`, `rejected`, `canceled` and `expired` are final; Execution stops polling an order once it
reaches one. An order rejected by the broker at submission (e.g. insufficient buying power) is
recorded directly as `rejected`, with `broker_order_id` `NULL` and the reason in `broker_reason`.

## New table: `execution_refusals` (E4)

One approved verdict Execution declined to submit (spec FR-007). Insert-only.

| Field | Type | Constraints |
|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` |
| `risk_verdict_id` | uuid | NOT NULL, `UNIQUE` |
| `verdict` | text | NOT NULL, default `'approved'`, `CHECK (verdict = 'approved')` |
| `reason` | text | NOT NULL, one of the names in [contracts/refusal-reasons.md](contracts/refusal-reasons.md) |
| `details` | jsonb | NOT NULL, a JSON object: the live numbers Execution saw (e.g. quote, ceiling, equity, line) |
| `refused_at` | timestamptz | NOT NULL |

Foreign key `(risk_verdict_id, verdict) REFERENCES risk_verdicts (id, verdict)`, the same composite
key `orders` uses, so a refusal can only name an approved verdict.

**One outcome per approval.** An approved verdict ends with an order *or* a refusal, never both.
`UNIQUE (risk_verdict_id)` on each table prevents two of the same kind. A `BEFORE INSERT` trigger on
each table rejects the row if the other table already has one for the same verdict
(`execution_outcome_exclusive`). Execution also serializes its own work (E5), so the triggers are a
second guard rather than the only one.

Index: none beyond the unique key. The table grows by at most a few rows a day.

## Changed grants on existing objects (E10, spec FR-017, FR-018)

| Object | `ta_execution` before | after |
|---|---|---|
| `orders` | S, I, U | S, I, **U(broker_order_id, status, fill_qty, fill_price, broker_reason, updated_at)** |
| `execution_refusals` | — | **S, I** |
| `system_state` | — | **S(trading_paused)**: the manual pause flag only (FR-018) |

Every other role that reads `orders` gains `SELECT` on `execution_refusals` too (the journal, the
Assistant, the dashboard), so an approval that never traded is visible wherever orders are.
Nothing else changes: `ta_execution` keeps `SELECT` on `risk_verdicts`, `S, I, U, D` on
`positions`, `S, I` on `account_snapshots` and `stop_loss_triggers`.

## Existing tables Execution writes, and how

| Table | Written when | Notes |
|---|---|---|
| `account_snapshots` | each trading day's pre-open run; immediately before every buy | Values exactly as fetched from the broker, never computed (FR-015) |
| `stop_loss_triggers` | a monitor cycle finds a held position at or below its line | `observed_price` is the last traded price (Clarifications) |
| `positions` | an order's fill quantity increases; reconciliation with the broker | Weighted average entry on buys; unchanged on sells; row deleted at zero (FR-011) |
| `orders` | submission, then each status change | See above |
| `execution_refusals` | a final refusal | See above |

## Relationships

```text
decisions ─┐
           ├─► risk_verdicts (approved) ──┬─► orders             (at most one)
stop_loss_ ┘                              └─► execution_refusals  (at most one; never both)
triggers
```

## Migration `0008_execution_review.sql` (research E16)

| Change | Detail |
|---|---|
| `execution_refusals.reason` | CHECK widened with `invalid_symbol` (contracts/refusal-reasons.md) |

The gate's new `stop_loss_trigger_stale` rule needs no schema change: `risk_verdicts.rejection_rule`
is free text checked by the rules contract, and `stop_loss_triggers.observed_at` already exists.
