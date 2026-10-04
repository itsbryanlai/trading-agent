# Contract: Risk Gate Interface

What other features call, and what they can rely on. The pure core is described by its inputs and
outputs, not its code. Decisions behind it: [research.md](../research.md) G1–G13.

## Entry points (service, `trading_agent.risk.service`)

Both run as `ta_risk_gate`, inside one transaction holding the gate's advisory lock (G10):

| Call | Used by | Behavior |
|---|---|---|
| `evaluate_decision(decision_id) -> Verdict \| None` | the gate's own loop (`python -m trading_agent.risk`, `trading_agent.risk.runner.evaluate_pending_decisions`), for the PM's buy and sell decisions from the current trading day that have no verdict yet. Amended by `specs/008-portfolio-manager` ([ADR 0019](../../../docs/adr/0019-the-gate-evaluates-pm-decisions-in-its-own-loop.md)); the PM never calls it. | Returns `None` for a `hold` decision and writes nothing. Otherwise returns the verdict, recording it if new. |
| `evaluate_stop_loss_trigger(trigger_id) -> Verdict` | the gate's own trigger runner (`python -m trading_agent.risk`, `trading_agent.risk.runner`), for triggers Execution's monitor recorded. Amended by `specs/003-execution` (Clarifications 2026-09-28, ADR 0013): Execution never calls it. | Returns the verdict, recording it if new. |

Guarantees:
- **Idempotent** (FR-017): calling either again for the same id returns the recorded verdict
  unchanged and writes nothing.
- **Serialized** (G10): no two evaluations interleave, so the daily order cap can't be exceeded by
  a race.
- **Config failures abort** (FR-014): if `config/risk.yaml` is missing or invalid, the call raises
  `RiskConfigError` naming the setting, and nothing is written.
- **No broker, no model, no network** in either call. Its only external input is the exchange
  calendar library, which is computed locally.

## Pure core (`trading_agent.risk.gate`)

`evaluate(request, context, config) -> GateResult`

**`request`**: exactly one of:
- a decision: `symbol`, `direction` (`buy` | `sell`), `target_weight_pct` (the decision's
  `size_pct`), `quote`
- a stop-loss trigger: `symbol`, `observed_price`

**`context`**: everything else, as plain values:
`now`, `trading_day`, `market_open`, `trading_paused`, `halt_active`, `shares_held`,
`avg_entry_price` (if held), `equity` and `cash` from today's latest snapshot (or none),
`baseline_equity` (or none), `increase_orders_approved_today`, `reference` (the universe row for the
symbol today, or none).

**`config`**: a validated `RiskConfig` (contracts/risk-config.md).

**`GateResult`**:
- `verdict`: `approved` with an `approved_order` (shape in
  [data-model.md](../data-model.md#approved_order-json-shape)), or `rejected` with
  `rejection_rule` (one name from contracts/rejection-rules.md)
- `record_halt`: true when this evaluation, whatever the request type, found today's equity at or
  below the loss line and the halt isn't active yet. It never changes an exit's verdict.
- `config_version`, `trading_day`: copied onto the verdict

Guarantees:
- Identical `(request, context, config)` produce an identical `GateResult` (FR-002, SC-002).
- An approved buy never takes the position above `max_position_pct` of equity, nor cash below
  `cash_reserve_pct` of equity, when filled at any price up to its `limit_price` (FR-001a, SC-001).
- An approved sell or exit never sells more shares than `shares_held` (FR-006).
- Exits are rejected only for `market_closed`, `no_position`, `stop_loss_not_breached`,
  `no_account_snapshot_today` (partial sells only), `direction_contradicts_target`, or
  `target_already_met`. They are never rejected by a halt, the pause, the order cap, a missing
  baseline, or the universe (SC-003).

## Baseline helper

`choose_baseline(stored_for_today, latest_snapshot_before_open) -> (equity | None, record: bool)`.
The stored value wins. Otherwise the pre-open snapshot's equity is used and marked for recording.
If there is neither, `(None, False)` (G8).

## What callers must *not* do

- Construct an `approved_order` themselves, or alter one after the gate returns it.
- Submit an approved order whose `trading_day` isn't today, or when the market is closed (FR-019).
  Enforced by Execution.
- Read `config/risk.yaml` from anywhere outside `trading_agent.risk`. This is enforced by an
  import-scan test; the Portfolio Manager in particular must never read it.
