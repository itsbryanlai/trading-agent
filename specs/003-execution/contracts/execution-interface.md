# Contract: Execution Interface

What other features call or rely on. Decisions: [research.md](../research.md) E1–E13.

## Entry points (`trading_agent.execution`)

| Call | Used by | Behaviour |
|---|---|---|
| `python -m trading_agent.execution` | the worker service (hosting is the orchestrator feature's call, E13) | Runs `startup()`, then `tick()` every 60 seconds until stopped. |
| `startup(broker, exec_conn)` | the runner | Paper-only guard (FR-013): raises `NotPaperTrading` and nothing else runs. Then one sync and reconciliation (FR-009). |
| `tick(now, broker, exec_conn, gate_conn, config_path)` | the runner, or the orchestrator's scheduler | One pass of every duty that's due at `now`, in the order listed in E13. Returns a `TickReport` (counts of orders submitted, refusals, retries, fills applied, triggers recorded, snapshot taken) for logging. |

Connections: `exec_conn` is a login in `ta_execution`. `gate_conn` is a login in `ta_risk_gate`,
used **only** to call `evaluate_stop_loss_trigger` (E13).

Guarantees:
- **At most one order per approved verdict, ever**, across restarts and crashes at any point (E5,
  SC-002).
- **Every approved verdict ends with exactly one outcome** once its trading day is over: an
  `orders` row or an `execution_refusals` row, never both (E4).
- **Only approved verdicts from today, while the market is open**, reach the broker (FR-001,
  FR-002). The composite foreign key on both tables makes an outcome for a rejected verdict
  impossible at the database.
- **No order is constructed**: side, symbol and quantity come from the verdict unchanged; a buy's
  limit price is the live ask, at or under the verdict's ceiling (FR-003).
- **Serialized**: one approval at a time, under an advisory lock distinct from the gate's (E5).
- **Deterministic** given the same database rows, broker responses, config and `now` (FR-016).
  No model call.

## What Execution relies on from the Risk Gate (feature 002)

- `approved_order` JSON exactly as in [002's data model](../../002-risk-gate/data-model.md):
  `symbol`, `side`, `qty`, `order_type`, `limit_price` (buys), `trading_day`, `exposure`,
  `source`, `time_in_force`.
- `evaluate_stop_loss_trigger(gate_conn, trigger_id, now=...)` returns the verdict, idempotently.
- The baseline rule (002 G8), which Execution re-derives itself (E11).

## What other components can rely on from Execution

| Reader | Relies on |
|---|---|
| Risk Gate | an `account_snapshots` row on each trading day before the open; one before every buy; `positions` reconciled to the broker at least every tick; stop-loss triggers only for positions at or below the line by the last traded price |
| Portfolio Manager, journal, Assistant, dashboard | `orders` with broker-confirmed status and fills; `execution_refusals` naming why an approval didn't trade ([refusal-reasons.md](refusal-reasons.md)) |
| Orchestrator | nothing: Execution finds approvals itself each tick, so no hand-off is needed |

## What callers must *not* do

- Pass `gate_conn` for anything but the stop-loss evaluation, or give any other component the
  broker port or its keys.
- Import `trading_agent.execution.alpaca` from outside `trading_agent.execution` (enforced by an
  import-scan test, E2).
