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

A limit order at the current quote (fetched at submission time, not reused
from an earlier step) with a day time-in-force. No order-splitting, no
price-improvement logic, no held/working orders across sessions — see
[ADR 0005](../adr/0005-risk-gate-and-execution-are-deterministic.md) for why
this fixed rule is sufficient at this trading pace.

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

## Interfaces

- Reads `risk_verdicts` (approved rows only), `positions`.
- Writes only `orders` and updates to `positions` from confirmed fills.
- The only role with Alpaca broker credentials in the entire system.

## Non-goals

- Does not evaluate risk limits itself beyond re-deriving the same numbers
  the Risk Gate already checked — it is not a second, independent policy
  layer, just a distrustful re-read of the same facts.
- Does not manage working/resting orders across multiple sessions.
