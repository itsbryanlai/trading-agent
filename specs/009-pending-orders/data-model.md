# Data model: The Risk Gate counts orders still in flight

No new table. Migration 0013 adds one view and one grant. Details: [research I1](research.md#i1-a-narrow-view-not-table-grants).

## Migration 0013: `in_flight_orders`

A view owned by the migration admin, run with its owner's rights (no `security_invoker`), as `reference_candidate_symbols` is (migration 0009).

| Column | Type | Source |
|---|---|---|
| `trading_day` | date | `risk_verdicts.trading_day` |
| `symbol` | text | `risk_verdicts.approved_order->>'symbol'` |
| `side` | text | `risk_verdicts.approved_order->>'side'` (`buy` or `sell`) |
| `unsettled_qty` | numeric | `(approved_order->>'qty')::numeric − coalesce(orders.fill_qty, 0)`, only rows above 0 |
| `limit_price` | numeric | `(approved_order->>'limit_price')::numeric` for a buy; null for a sell |

Rows: every `risk_verdicts` row with `verdict = 'approved'` that either has no `orders` row and no `execution_refusals` row, or whose `orders.status` is `submitted` or `partially_filled`.

**Not exposed**: verdict ids, decision or trigger ids, broker order ids, fill prices, refusal reasons, timestamps.

**Grants**: `SELECT` to `ta_risk_gate`, `ta_assistant` and `ta_dashboard` (the last two read everything, Constitution VII). No other change. `specs/001-data-model/contracts/role-grants.md` and the integration grants matrix gain the row.

## Risk Gate context (in memory)

`risk.model.Context` gains `in_flight_buy_qty`, `in_flight_sell_qty` (this symbol, today) and `in_flight_buy_cost` (every symbol, today, at each approval's ceiling), all `Decimal`, default 0. Read in `risk.service._load_context` (research I3).

## Unchanged

`risk_verdicts`, `orders`, `execution_refusals`, `positions`, `system_state`, `config/risk.yaml`, every other role's grants.
