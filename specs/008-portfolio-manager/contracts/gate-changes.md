# Contract: changes to the Risk Gate (feature 002)

Every change here is order logic, flagged before it's applied (CLAUDE.md), and recorded in [ADR 0019](../../../docs/adr/0019-the-gate-evaluates-pm-decisions-in-its-own-loop.md) before any code changes. Nothing in `config/risk.yaml` changes. Details: [research P12](../research.md#p12-the-gates-loop-evaluates-decisions-decision_stale).

## Who calls `evaluate_decision`

| Before (`specs/002-risk-gate/contracts/gate-interface.md`) | After |
|---|---|
| "the PM session runner (orchestrator feature)", which was never built | the gate's own loop, `python -m trading_agent.risk`, through `runner.evaluate_pending_decisions` |

`evaluate_decision` itself is unchanged: idempotent, advisory-locked, `None` for a hold.

## `runner.evaluate_pending_decisions(conn, now, config_path=…) -> int`

- **Picks**: `decisions` whose New York date of `generated_at` is `now`'s trading day, `direction <> 'hold'`, and no `risk_verdicts` row; ordered by `generated_at, id`.
- **Earlier days**: never picked (the query filters to today), so never evaluated and never logged each pass.
- **Isolation**: one decision's unexpected error is logged and the rest still run. `psycopg.OperationalError` propagates (the loop exits for a restart). `RiskConfigError` stops the pass, logged once.
- **Returns** how many were evaluated. Logs `risk gate: decision <id> approved` or `… rejected (<rule>)`.

## The loop (`risk/__main__.py`)

Each pass, every `PASS_SECONDS` (60): pending stop-loss triggers first, then pending decisions.

## New rule: `decision_stale`

| # | Rule | Rejects when |
|---|---|---|
| 1a | `decision_stale` | *decision only, buy or sell*: `now − quote_time > MAX_DECISION_QUOTE_AGE` (15 minutes, a code constant beside `MAX_TRIGGER_AGE`). The PM's next run decides again on a fresh quote. |

- **Precedence**: the first rejection after `market_closed`, before every other rule, for buys and sell decisions alike. It's checked after the core computes whether the loss line is crossed, and returns `record_halt` with that result, so a stale decision still records the daily-loss halt (the 002 contract: "on every evaluation").
- **Stop-loss triggers**: unaffected; they keep `stop_loss_trigger_stale`.
- **Exits**: the "Exits" section's list of what can reject an exit gains `decision_stale`, for sell decisions only.

## `DecisionRequest`

Gains `quote_time: datetime` (aware), read from `decisions.quote_time` (migration 0012).
