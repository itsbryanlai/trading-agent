# Contract: Rejection Rules

The exact strings the gate writes to `risk_verdicts.rejection_rule`, and to `approved_order.trims`.
The dashboard, the Assistant, and the journal display and count these, so they are part of the
interface: renaming one is a contract change. Precedence is from [research.md](../research.md) G5.
When several apply, the verdict names the one listed first for its request type.

## All requests

| # | Rule | Rejects when |
|---|---|---|
| 1 | `market_closed` | the exchange calendar says the market is not open at evaluation time (FR-007) |

## Exits: sell decisions and stop-loss triggers

| # | Rule | Rejects when |
|---|---|---|
| 2a | `stop_loss_trigger_stale` | *trigger only*: observed more than 10 minutes before this evaluation ([ADR 0014](../../../docs/adr/0014-fresh-confirmed-stop-loss-triggers-and-intraday-equity.md); added by `specs/003-execution`). The monitor records a new trigger if the breach is still real. |
| 2 | `no_position` | no shares of the symbol are held (FR-006, FR-012) |
| 3 | `stop_loss_not_breached` | *trigger only*: observed price is above `avg_entry_price × (1 − stop_loss_pct/100)` (FR-012) |
| 4 | `no_account_snapshot_today` | *sell decision with a target above 0% only*: no account snapshot on today's trading day, so the shares to keep can't be computed (FR-018). A 0% sell and a stop-loss exit never hit this. |
| 5 | `direction_contradicts_target` | *sell decision only*: the target is more than one share *above* the holding (FR-003) |
| 6 | `target_already_met` | *sell decision only*: fewer than one share to sell to reach the target (FR-003) |

Otherwise approved as a market sell. Nothing else can reject an exit: not the pause, the halt, the
order cap, a missing baseline, or the universe.

## Recording the halt (not a rejection)

On **every** evaluation, whether buy, sell, or trigger, where today's baseline and today's equity
are both known, equity at or below `baseline × (1 − daily_loss_halt_pct/100)` records the halt for
today if it isn't already recorded (FR-009). For a buy, this is also the rejection
`daily_loss_halt` below. For an exit, it changes nothing about the verdict.

## Buys

| # | Rule | Rejects when |
|---|---|---|
| 2 | `trading_paused` | the manual pause is on (FR-020) |
| 3 | `no_account_snapshot_today` | no account snapshot on today's trading day (FR-018) |
| 4 | `no_daily_baseline` | no baseline stored and no snapshot before today's open (FR-013) |
| 5 | `daily_loss_halt` | the halt is active, or this evaluation finds equity ≤ baseline × (1 − `daily_loss_halt_pct`/100), which also records it (FR-008, FR-009) |
| 6 | `daily_order_cap` | `max_orders_per_day` exposure-increasing orders are already approved today (FR-010) |
| 7 | `universe_no_reference_data` | no reference row for the symbol on today's trading day (FR-011) |
| 8 | `universe_listing` | not `common_stock` on XNYS, XNAS or XASE (FR-011) |
| 9 | `universe_market_cap` | market cap below `min_market_cap_usd` |
| 10 | `universe_dollar_volume` | average daily dollar volume below `min_avg_daily_dollar_volume_usd` |
| 11 | `universe_share_price` | share price below `min_share_price_usd` |
| 12 | `direction_contradicts_target` | the target is more than one share *below* the holding (FR-003) |
| 13 | `target_already_met` | fewer than one share to buy to reach the target (FR-003) |
| 14 | `max_position_pct` | no room under the per-symbol ceiling, valued at the price ceiling (FR-004) |
| 15 | `cash_reserve_pct` | no room above the cash floor, valued at the price ceiling (FR-005) |

Otherwise approved as a limit buy, with `trims` listing whichever of `max_position_pct` and
`cash_reserve_pct` lowered the quantity.

## Not verdicts

- `hold` decisions produce no verdict (FR-001).
- An invalid or missing config raises `RiskConfigError`; no verdict is written (FR-014).
