# Contract: changes to the Risk Gate (feature 009)

Order logic, flagged before it's applied (CLAUDE.md), recorded in [ADR 0020](../../../docs/adr/0020-the-gate-counts-orders-in-flight.md) before any code changes. `config/risk.yaml` is unchanged. Details: [research I3–I7](../research.md).

## `gate-interface.md` (`specs/002-risk-gate/contracts/`)

- **`context`** gains `in_flight_buy_qty`, `in_flight_sell_qty` and `in_flight_buy_cost` (research I3).
- **Guarantees** gain:
  - an approved buy, together with every in-flight buy, keeps the position under `max_position_pct` and cash above `cash_reserve_pct` at the price ceilings;
  - an approved sell never exceeds `shares_held − in_flight_sell_qty`;
  - with all three in-flight values at 0, every verdict is exactly as before.

## `rejection-rules.md`

No rule is added or renamed. What changes is the input to existing rules:

| Rule | Measured against |
|---|---|
| `direction_contradicts_target`, `target_already_met` (buys and sells) | settled holdings |
| `max_position_pct` | settled holdings |
| `cash_reserve_pct` | cash less in-flight buy cost |
| `target_already_met` (sells) | also when every held share is already being sold |
| `no_position`, `stop_loss_not_breached`, every account and universe rule | unchanged |

## Who reads what

| Role | Gains |
|---|---|
| `ta_risk_gate` | `SELECT` on `in_flight_orders` |
| `ta_assistant`, `ta_dashboard` | `SELECT` on `in_flight_orders` (Constitution VII) |
| every other role | nothing |
