# 0010. Stop-loss monitoring, universe reference data, and the daily baseline

Status: accepted

## Context

Specifying the Risk Gate (`specs/002-risk-gate`) exposed three inputs it needs
but, by design, cannot fetch: it holds no broker or market-data credential
([0005](0005-risk-gate-and-execution-are-deterministic.md)).

- The universe re-check needs each symbol's listing type, market cap, average
  daily dollar volume, and share price.
- The stop-loss rule needs current prices of held positions. The gate only runs
  when the Portfolio Manager makes a decision, which can't enforce a stop.
- The market-open signal and the daily-loss breaker's starting equity
  ([0006](0006-autonomous-operation-with-daily-loss-breaker.md)) had no named
  source.

## Decision

1. **Stop-loss monitor inside Execution, routed through the gate.** Execution
   already holds the broker credential, and with it prices. Every 30 minutes
   during market hours it checks each held position. When one is at or below the
   stop-loss line, it records a *stop-loss trigger* with the price it observed.
   The Risk Gate evaluates the trigger and re-checks the drop itself from the
   recorded price and the position's average entry price. A genuine breach gets
   an approved full exit that neither the daily order cap nor the daily-loss
   halt can block; anything else is rejected. Execution then submits only what
   was approved. This keeps Constitution Principle I intact: every order passes
   the gate, and nothing else constructs one.
2. **Stop-loss distance raised from 8% to 20%** below average entry price, at
   the owner's request. This loosens a risk limit, so it is recorded here and in
   `config/risk.yaml`'s review history rather than changed silently.
3. **A daily universe reference-data job.** A small deterministic job, a new
   component with its own database role, runs once per trading day. It uses a
   read-only Finnhub key that cannot trade
   ([0008](0008-dashboard-stack-and-research-provider.md)) to record each
   symbol's listing type, market cap, average daily dollar volume, and share
   price. The gate reads this table. A symbol without data from the current
   trading day fails the universe check.
4. **Market open from an exchange calendar; baseline from the pre-open
   snapshot.** The gate's caller works out whether the market is open from an
   exchange-calendar library (hours, holidays, early closes), with no
   credential. The daily-loss baseline is the last account snapshot taken
   before that trading day's open. Execution must record one every trading day.

## Alternatives considered

- **Broker-held stop orders** placed by Execution at each fill. The broker
  enforces them continuously, even while this system is down, and no polling is
  needed. Not chosen: the owner preferred monitoring on a fixed schedule.
  Revisit if the gap between checks proves costly.
- **Execution submitting stop-loss exits directly**, without the gate. Rejected
  outright: it would violate Principle I (non-negotiable).
- **Universe data carried on the PM's decision.** Rejected: the gate would be
  trusting upstream data, which Principle VI forbids.
- **Market-open status from the broker's clock**, written by Execution.
  Rejected: it adds a stored status that can go stale, and a dependency on
  Execution staying up, when a calendar answers the same question
  deterministically.

## Consequences

- Until the reference-data job exists, every buy fails the universe check. That
  fails closed, so buys can't happen before it is built.
- A position can fall well past 20% between two 30-minute checks, and overnight
  gaps happen while no check runs at all. The realised exit can be materially
  worse than the configured line.
- `risk_verdicts` can now come from a stop-loss trigger as well as a decision.
  The data model gains a triggers table and a reference table (feature 002).
- The Execution feature inherits three duties: the 30-minute monitor, the daily
  pre-open account snapshot, and submitting only gate-approved exits.
