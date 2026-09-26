# Orchestrator

## Purpose

Sequences when Research, the Opportunistic Identifier, and the PM run.
Nothing else. See
[ADR 0003](../adr/0003-orchestrator-is-a-scheduler-not-an-authority.md) for
why it deliberately has no data access and no authority over any agent's
output.

## Inputs

- A schedule config (per-agent cadence: Research ~daily + news-triggered,
  Opportunistic Identifier intraday polling interval, PM once daily).
- The exchange calendar, to skip days the market is closed — checked next to
  the schedule it guards, same pattern as `trading-bot`.
- `system_state.trading_paused` — the one piece of state it reads, solely to
  decide whether to invoke the PM this cycle.

## Outputs

Invocations of each agent's entry point, at the scheduled time. No database
writes beyond, at most, its own run/heartbeat bookkeeping (not part of the
shared knowledge base's trading tables).

## Edge cases

- **`trading_paused` is set**: skip the PM invocation entirely for the
  duration of the pause; Research and the Opportunistic Identifier continue
  running on their own schedules regardless (they only produce reports, so
  pausing trading doesn't need to pause them).
- **An agent invocation fails or times out**: log the failure and continue
  the schedule — one agent's failure must not block another's scheduled run,
  and must not silently retry into a runaway loop.
- **Process restart mid-day**: re-derive today's schedule from the exchange
  calendar and current time rather than trusting any in-memory state from
  before the restart, same as `trading-bot`'s scheduler.

## Interfaces

- No database credentials beyond reading `system_state.trading_paused` and
  the exchange calendar (external, not part of the shared knowledge base).
- Never reads `reports`, `decisions`, `risk_verdicts`, `orders`, or
  `positions`. Never makes a model call itself.

## Non-goals

- Does not review, approve, or alter any agent's output.
- Is not a supervisor and has no authority any agent must defer to — see
  [ADR 0003](../adr/0003-orchestrator-is-a-scheduler-not-an-authority.md)'s
  explicit rejection of that shape.
