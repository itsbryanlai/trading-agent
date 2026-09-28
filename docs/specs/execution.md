# Execution

## Purpose

The only component in the system that holds broker credentials and the only
one that may submit an order. See
[ADR 0005](../adr/0005-risk-gate-and-execution-are-deterministic.md) for why
this is deterministic rather than an LLM agent with its own judgment.

Not responsible for: deciding direction/size (PM), deciding whether a
decision is allowed (Risk Gate) — it accepts only an already-approved
verdict and turns it into a broker call.

## Inputs

- One `risk_verdicts` row with `verdict = 'approved'`.
- Its own fresh read of `positions` and account cash, re-derived independently
  rather than trusted from the verdict it was handed — same discipline as
  `trading-bot`'s `order_client`, and a deliberate second check even though
  the Risk Gate already validated the same limits moments earlier.
- Alpaca paper-trading credentials (the only credentials this component
  holds).

## Outputs

One `orders` row per submission, with a deterministic id
`{trading_day}-{symbol}-{side}-{first 8 hex of the verdict id}`
([ADR 0012](../adr/0012-order-identifier-per-verdict.md)), also sent to the
broker as its client order id. A process restarting after a crash re-derives
the same id and looks it up at the broker before submitting anything, so it
finds the existing order instead of opening a second position.

Or one `execution_refusals` row when it declines an approval, naming the reason
(`specs/003-execution/contracts/refusal-reasons.md`). Every approval ends with
exactly one of the two.

## Order construction rule

- **Buys**: a day limit order at the live ask (IEX feed, fetched at submission
  time and no more than 60 seconds old), submitted only if it is at or under the
  price ceiling in the Risk Gate's approved order. Above the ceiling, the buy is
  refused for good; the next Portfolio Manager run decides again. Buys are also
  refused while the owner has trading paused, and once any account snapshot
  since the open has been at or below the daily-loss line (the rest of the day,
  even if equity recovers).
- **Sells and stop-loss exits**: a day market order, so an exit is never left
  unfilled behind a limit in a falling market.

No order-splitting, no price-improvement logic, no held/working orders across
sessions. See [ADR 0005](../adr/0005-risk-gate-and-execution-are-deterministic.md)
for why fixed rules are sufficient at this trading pace, and
`specs/002-risk-gate` Clarifications for the ceiling/market split.

## Scheduled duties

Added by [ADR 0010](../adr/0010-stop-loss-monitor-and-universe-reference-data.md):

- **Stop-loss monitor**: every 30 minutes during market hours, check every held
  position against its average entry price. For each one at or below the
  configured stop-loss line (`stop_loss_pct`, 20%) by its last traded price,
  confirmed by its current bid also at or below the line (one odd print isn't
  enough, [ADR 0014](../adr/0014-fresh-confirmed-stop-loss-triggers-and-intraday-equity.md)),
  record a stop-loss trigger with that price. The gate rejects a trigger more
  than 10 minutes old; the monitor records a new one if the breach is still real. The Risk Gate evaluates it in its
  own process with its own credential
  ([ADR 0013](../adr/0013-deterministic-services-run-their-own-loops.md)), and
  Execution submits the exit on its next cycle only if the gate approved it.
  Execution never constructs an exit itself, and never holds the gate's
  credential.
- **An account snapshot every stop-loss window**: one per 30-minute window
  during market hours, so a crossing of the daily-loss line is recorded within
  30 minutes and no buy follows it that day (ADR 0014).
- **Daily pre-open account snapshot**: record an `account_snapshots` row every
  trading day before the market opens. The Risk Gate takes the daily-loss
  baseline from it, and without it rejects all new exposure that day.
- **Live check before every buy**: immediately before submitting an approved
  buy, fetch live account equity from the broker, record it as an
  `account_snapshots` row, and refuse to submit if equity is at or below the
  daily-loss line under today's baseline. Recording the halt stays with the
  Risk Gate, which does so from this snapshot at its next evaluation. Sells and
  stop-loss exits skip this check (`specs/002-risk-gate` Clarifications).

## Edge cases

- **Re-derived limits disagree with the verdict it was handed** (e.g. a fill
  from a concurrent order changed position size between Risk Gate evaluation
  and Execution's own read): refuse to submit and log the discrepancy —
  never submit against a verdict Execution's own numbers can't confirm.
- **Broker rejects the order** (e.g. halted symbol, insufficient buying
  power the broker itself computes differently): record the rejection on the
  `orders` row; this is not retried automatically within the same session.
- **Process crash after submission, before the fill is recorded**: the
  deterministic order id lets the restarted process discover the existing
  broker order rather than resubmitting.
- **The clock at submission**: the checks run at the start of a cycle, so the
  clock is read again just before each submission. Nothing is submitted after
  the close or in its final 30 seconds; Alpaca would hold a late day order for
  the next session, where it would go out unchecked.
- **A submission that timed out**: it may be live at the broker without being
  recorded. Until a lookup settles it, no other buy is submitted and no other
  exit of the same symbol if it was a sell, and a later rejection of the same
  identifier is treated as a duplicate of the live order, never recorded.
- **A symbol the order identifier can't hold** (e.g. `BRK-B`): refused as
  `invalid_symbol` before any broker call.
- **Two Execution processes**: only one runs; a second refuses to start.
- **Startup against a non-paper endpoint**: refuse to start at all. The paper
  address is fixed in code; a configured address that differs from it stops
  startup; and an authenticated account read at that address must succeed.
  Alpaca documents no account field that marks an account as paper, so the
  proof is that paper keys differ from live keys and this address serves only
  paper accounts (`specs/003-execution` research E2).
- **Approval from an earlier trading day**: never submit it. An approval is
  valid only on the trading day it was approved for; unsubmitted approvals
  lapse at the close (`specs/002-risk-gate` FR-019).

## Interfaces

- Runs as its own process (`python -m trading_agent.execution`), ticking once a
  minute and finding new approvals itself; nothing hands them over
  ([ADR 0013](../adr/0013-deterministic-services-run-their-own-loops.md)).
- Reads `risk_verdicts` (approved rows only), `positions`, and the manual pause
  flag (`system_state.trading_paused` only).
- Writes `orders`, `execution_refusals`, updates to `positions` from confirmed
  fills (reconciled to the broker's positions), `account_snapshots`, and
  stop-loss triggers.
- The only role with Alpaca broker credentials in the entire system.

## Non-goals

- Does not evaluate risk limits itself beyond re-deriving the same numbers
  the Risk Gate already checked — it is not a second, independent policy
  layer, just a distrustful re-read of the same facts.
- Does not manage working/resting orders across multiple sessions.
