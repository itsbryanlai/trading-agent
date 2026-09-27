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

One `orders` row per submission, with a deterministic id derived from trading
day + symbol + side — so a process restart after a crash re-derives the same
id and the broker rejects the duplicate rather than opening a second
position (same mechanism as `trading-bot`).

## Order construction rule

- **Buys**: a day limit order at the live quote (fetched at submission time),
  submitted only if that quote is at or under the price ceiling in the Risk
  Gate's approved order. If the live quote is above the ceiling, the buy is
  not submitted.
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
  configured stop-loss line (`stop_loss_pct`, 20%), record a stop-loss trigger
  with the observed price and hand it to the Risk Gate. Submit the exit only if
  the gate approves it. Execution never constructs an exit itself.
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
- **Startup against a non-paper endpoint**: refuse to start at all — same
  hard requirement as `trading-bot`.
- **Approval from an earlier trading day**: never submit it. An approval is
  valid only on the trading day it was approved for; unsubmitted approvals
  lapse at the close (`specs/002-risk-gate` FR-019).

## Interfaces

- Reads `risk_verdicts` (approved rows only), `positions`.
- Writes `orders`, updates to `positions` from confirmed fills,
  `account_snapshots`, and stop-loss triggers.
- The only role with Alpaca broker credentials in the entire system.

## Non-goals

- Does not evaluate risk limits itself beyond re-deriving the same numbers
  the Risk Gate already checked — it is not a second, independent policy
  layer, just a distrustful re-read of the same facts.
- Does not manage working/resting orders across multiple sessions.
