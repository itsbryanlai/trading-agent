# Data Model: Risk Gate

Changes this feature makes to the shared knowledge base
([feature 001 data model](../001-data-model/data-model.md)), all in one forward-only migration,
`0006_risk_gate.sql`. Decisions are cited as G-numbers from [research.md](research.md). The
permissions are in the amended [role-grants contract](../001-data-model/contracts/role-grants.md).

## New group role

| Role | Component | Why |
|---|---|---|
| `ta_reference_data` | Daily universe reference-data job (ADR 0010) | The only writer of `instrument_reference`. Created here so the table ships with its grants; the job itself is a later feature. |

## New table: `stop_loss_triggers` (G13)

One observation by Execution's 30-minute monitor that a held position is at or below its stop-loss
line. Insert-only.

| Field | Type | Constraints |
|---|---|---|
| `id` | uuid | PK, default `gen_random_uuid()` |
| `symbol` | text | NOT NULL |
| `observed_price` | numeric(14,4) | NOT NULL, `> 0` |
| `observed_at` | timestamptz | NOT NULL, default `now()` |

There is deliberately no entry price and no stop line. The gate re-derives both from `positions`
and its own config, and trusts only the observed price, which it cannot fetch itself.

Index `(observed_at DESC)`.

## New table: `instrument_reference` (G14)

Per-symbol universe data as of one trading day. Written by the reference-data job.

| Field | Type | Constraints |
|---|---|---|
| `symbol` | text | NOT NULL |
| `trading_day` | date | NOT NULL |
| `security_type` | text | NOT NULL, `IN ('common_stock', 'etf', 'adr', 'other')` |
| `exchange_mic` | text | NOT NULL |
| `market_cap_usd` | numeric(20,2) | NOT NULL, `>= 0` |
| `avg_daily_dollar_volume_usd` | numeric(20,2) | NOT NULL, `>= 0` |
| `share_price_usd` | numeric(14,4) | NOT NULL, `> 0` |
| `fetched_at` | timestamptz | NOT NULL, default `now()` |

PK `(symbol, trading_day)`. The gate reads only the row for the current trading day. A missing row
fails the universe check (`universe_no_reference_data`).

## Changed table: `risk_verdicts` (G12)

| Change | Detail |
|---|---|
| `decision_id` | becomes **nullable** (still `UNIQUE`, still a FK to `decisions`) |
| `stop_loss_trigger_id` | **new**, `uuid UNIQUE REFERENCES stop_loss_triggers (id)`, nullable |
| exactly one source | **new** `CHECK (num_nonnulls(decision_id, stop_loss_trigger_id) = 1)` |
| `trading_day` | **new**, `date NOT NULL`: the trading day the verdict is valid for (FR-019). Existing rows are backfilled from `evaluated_at` in New York time before `NOT NULL` is set. |
| `config_version` | **new**, `text NOT NULL`: the first 12 hex characters of the SHA-256 of `config/risk.yaml` (FR-015, G6). Existing rows are backfilled `'pre-002'`. |
| index | **new** `(trading_day, verdict)`, which drives the daily order-cap count |

Unchanged: the `rejection_rule`/`approved_order` CHECKs, `UNIQUE (id, verdict)`, and therefore
`orders`' composite foreign key to an approved verdict.

### `approved_order` JSON shape

Validated by the gate before insert; the database checks only that it is an object.

| Key | Buy | Sell / stop-loss exit |
|---|---|---|
| `symbol` | ✓ | ✓ |
| `side` | `"buy"` | `"sell"` |
| `qty` | whole shares, ≥ 1 | whole shares, ≥ 1 |
| `order_type` | `"limit"` | `"market"` |
| `limit_price` | price ceiling: quote × (1 + tolerance), **rounded down to the cent** so it never exceeds the tolerance; a decimal string | absent |
| `time_in_force` | `"day"` | `"day"` |
| `trading_day` | ISO date (duplicates the column for Execution's convenience) | same |
| `exposure` | `"increase"` | `"decrease"` |
| `trims` | list of limit names that lowered qty (e.g. `["max_position_pct"]`), possibly empty | `[]` |
| `source` | `"decision"` | `"decision"` or `"stop_loss"` |

`exposure` exists so the daily order cap (FR-010) counts `approved_order->>'exposure' = 'increase'`
without inferring intent from `side`.

## What the gate reads, per evaluation

| Input | Source | Notes |
|---|---|---|
| The request | `decisions` row, or `stop_loss_triggers` row | exactly one |
| Current holding | `positions` for the symbol | absent means 0 shares |
| Equity and cash | latest `account_snapshots` row whose `taken_at` is on today's NY trading day | none means `no_account_snapshot_today` (G9) |
| Baseline | `system_state.daily_starting_equity` when `baseline_trading_day` is today, else the latest snapshot taken on today's New York date before today's open | recorded by the service (G8). A snapshot from an earlier date is not used: it means Execution missed its pre-open duty, and the gate fails closed |
| Halt, pause | `system_state`, judged against the evaluation's own trading day | the same rule as `system_state_effective`, but computed from `now` rather than the database clock, so a verdict depends only on the gate's inputs (FR-002) |
| Approvals so far today | `count(*)` of `risk_verdicts` where `trading_day = today`, `verdict = 'approved'`, `approved_order->>'exposure' = 'increase'` | read under the advisory lock (G10) |
| Universe data | `instrument_reference` row for (symbol, today) | buys only |
| Config | `config/risk.yaml` via `RiskConfig` | not a database read |
| Clock, market open | `exchange_calendars` XNYS | computed by the service |

## What the gate writes, per evaluation (one transaction)

- Exactly one `risk_verdicts` row (unless one already exists, in which case nothing is written).
- If the baseline wasn't yet stored for today: `system_state.baseline_trading_day`,
  `daily_starting_equity`, `updated_at`.
- If the loss line was crossed and the halt isn't already recorded for today:
  `system_state.halt_triggered_on`, `updated_at`.

All of these are within `ta_risk_gate`'s existing column grants except the new `SELECT`s listed in
the contract.
