# Data Model: Universe Reference-Data Job

One existing table is written, one new view is read, and one grant is revoked. Migration `0009_reference_data.sql`; decisions in [research.md](research.md) D9–D10.

## `instrument_reference` (existing, migration 0006; unchanged shape)

| Column | Type | Written as |
|---|---|---|
| `symbol` | text | The ticker as named upstream (upper case, validated by FR-003) |
| `trading_day` | date | `calendar.trading_day(now)` at insert (ET) |
| `security_type` | text, CHECK in (`common_stock`, `etf`, `adr`, `other`) | D3 mapping |
| `exchange_mic` | text | D4 mapping (Nasdaq tiers → `XNAS`; others verbatim) |
| `market_cap_usd` | numeric(20,2) ≥ 0 | `marketCapitalization` × 10⁶; the job requires > 0 and ≤ $20 trillion (D5) |
| `avg_daily_dollar_volume_usd` | numeric(20,2) ≥ 0 | `10DayAverageTradingVolume` × 10⁶ × previous close; must be > 0 and ≤ market cap (D5) |
| `share_price_usd` | numeric(14,4) > 0 | Previous close `pc` |
| `fetched_at` | timestamptz | Database default `now()` |

Primary key `(symbol, trading_day)`. Rules:

- Inserted with `ON CONFLICT (symbol, trading_day) DO NOTHING`; never updated or deleted (FR-011, FR-022). After 0009 the writer role has no UPDATE.
- Never inserted for any day but today's (FR-006).
- Values are rounded to the column scale with `ROUND_HALF_EVEN` before insert, so the stored value is what the gate compares; a value that no longer fits its column, or a price that rounds to 0, is `value_out_of_range` (D5).
- A non-connection database error on one insert is that symbol's failure (`database_error`), never a process exit (D9).
- History is kept; the Assistant and dashboard can show past days.

## `reference_candidate_symbols` (new view)

The only thing the job reads besides `instrument_reference`. Symbols and timestamps only.

| Column | Type | Meaning |
|---|---|---|
| `symbol` | text | A symbol that is held or was named |
| `source` | text: `position` \| `report` \| `decision` | Where it came from; a symbol can appear once per source |
| `named_at` | timestamptz, null for `position` | Latest `generated_at` for that symbol and source |
| `active_until` | timestamptz, `report` only | Latest `expires_at` among its reports |

Definition (shape; the migration is the source):

- `position`: every `positions.symbol`.
- `report`: `reports` with `symbol IS NOT NULL`, grouped by symbol.
- `decision`: `decisions`, grouped by symbol.

No time filter: the exact window is applied in code, and a database-clock filter would break fixed-date tests and drop long-lived active reports (D10).

Owned by the migration admin; not `security_invoker`, so it reads the base tables with the owner's rights. `reports`' row-level security is enabled, not forced, so the owner-run view sees all rows; the policy itself is unchanged.

The job narrows to the FR-001 window in code (D10, `calendar.previous_session`): a report symbol qualifies if `active_until > now` or `named_at ≥ previous session's open`; a decision symbol if `named_at ≥ previous session's open`; a position always.

## Grants (amends `specs/001-data-model/contracts/role-grants.md`)

| Object | ta_reference_data | ta_assistant | ta_dashboard | everyone else |
|---|---|---|---|---|
| `instrument_reference` | S, I (**U revoked**) | S | S | unchanged (ta_risk_gate S) |
| `reference_candidate_symbols` (view) | S | S | S | — |

`positions`, `reports`, `decisions`: ta_reference_data stays at no access.

## In-memory state (not stored)

| State | Lives | Lost on restart means |
|---|---|---|
| Today's US symbol list (type, MIC per symbol) | One fetch per trading day | One extra bulk call |
| Per-symbol backoff (next attempt time, failure count) | Service | A few early retries |
| Key-rejected backoff | Service | One early retry |
| "Open warning done today" | Service | Warning may repeat once |
