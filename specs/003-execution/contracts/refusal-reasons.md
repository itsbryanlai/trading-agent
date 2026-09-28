# Contract: Refusal Reasons

The exact strings Execution writes to `execution_refusals.reason`. The dashboard, the Assistant, and
the journal display and count these, so they are part of the interface: renaming one is a contract
change, and the table's `CHECK` lists exactly these. Order of evaluation is from
[research.md](../research.md) E6; the first that applies is recorded.

A refusal is **final**: the approval is spent and is never submitted afterwards. Conditions that
are only temporary (broker unreachable, no usable live price, market not open *yet*, a risk config
that fails to load) are **not** refusals. They are logged and retried next tick while the approval
is still valid.

## Buys

| # | Reason | Refused when | `details` carries |
|---|---|---|---|
| 1 | `approval_expired` | the verdict's trading day is before today, or is today and the market has closed (FR-002) | `trading_day`, `now` |
| 1a | `invalid_symbol` | the order identifier can't be formed validly: the symbol isn't `[A-Z][A-Z0-9.]*` (FR-007). Checked before any broker call, so no order can be placed that couldn't be recorded. | `symbol`, `order_id` |
| 2 | `identifier_clash` | an order with this verdict's identifier already exists for a different verdict (FR-008) | `order_id`, `other_verdict_id` |
| 3 | `trading_paused` | the manual pause is on (FR-018) | — |
| 4 | `no_daily_baseline` | no snapshot on today's date before today's open (FR-004) | `trading_day` |
| 5 | `daily_loss_line_crossed` | live equity, or the lowest snapshot equity since today's open, ≤ baseline × (1 − `daily_loss_halt_pct`/100) (FR-004) | `equity`, `min_equity_since_open`, `min_snapshot_id`, `baseline`, `line`, `snapshot_id` (the one just recorded) |
| 6 | `quote_above_ceiling` | live ask > the verdict's price ceiling (FR-003) | `ask`, `ceiling`, `quote_time` |
| 7 | `max_position_pct` | (held + open buy qty + qty) × ask > `max_position_pct`% of live equity (FR-005) | `held`, `open_buy_qty`, `qty`, `ask`, `equity`, `limit_pct` |
| 8 | `cash_reserve_pct` | cash − open buy cost − qty × ask < `cash_reserve_pct`% of live equity (FR-005) | `cash`, `open_buy_cost`, `qty`, `ask`, `equity`, `reserve_pct` |

## Exits: sells and stop-loss exits

| # | Reason | Refused when | `details` carries |
|---|---|---|---|
| 1 | `approval_expired` | as for buys | `trading_day`, `now` |
| 1a | `invalid_symbol` | as for buys | `symbol`, `order_id` |
| 2 | `identifier_clash` | as for buys | `order_id`, `other_verdict_id` |
| 3 | `shares_held_differ` | the broker's held qty minus Execution's open sell qty is less than the approved qty (FR-006) | `held`, `open_sell_qty`, `qty` |

Nothing else refuses an exit: not the pause, the daily-loss line, a missing baseline, or a bad risk
config.

## Conventions for `details`

- A JSON object. Money and prices are decimal strings (e.g. `"201.50"`), never floats, matching
  `approved_order`.
- Times are ISO 8601 with offset.
- It records what Execution *saw*, so a refusal can be explained later without the broker.
