# Orchestrator

## Purpose

Sequences when Research, the Opportunistic Identifier, and the PM run.
Nothing else. See
[ADR 0003](../adr/0003-orchestrator-is-a-scheduler-not-an-authority.md) for
why it deliberately has no data access and no authority over any agent's
output.

## Inputs

- A schedule config (per-agent cadence: Research ~daily + news-triggered,
  Opportunistic Identifier intraday polling interval, PM morning session time,
  minimum spacing between PM runs (30 min), and the PM's last-run cutoff
  (15:30 ET), per [ADR 0011](../adr/0011-event-driven-portfolio-manager-runs.md)).
- The time the most recent report was written, and nothing else about reports.
  It uses this to trigger an event-driven PM run when a new report has arrived
  since the PM last ran.
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
  before the restart, same as `trading-bot`'s scheduler. The time of the PM's
  last run is also re-derived after a restart, not held only in memory, so a
  restart can neither skip nor double-fire an event-driven run.
- **Several reports arrive within 30 minutes of the last PM run**: one PM run
  once the 30 minutes are up, covering all of them.
- **A new report arrives after 15:30 ET**: no PM run. The report expires unused,
  by design (ADR 0011).

## Interfaces

- Reads `system_state.trading_paused`, the exchange calendar (external), and
  the latest report's creation time. The last should be exposed as a narrow
  read, e.g. a single-value view, rather than a grant on `reports` itself,
  since the orchestrator must never see report contents.
- Never reads report contents, `decisions`, `risk_verdicts`, `orders`, or
  `positions`. Never makes a model call itself.

## Non-goals

- Does not review, approve, or alter any agent's output.
- Is not a supervisor and has no authority any agent must defer to — see
  [ADR 0003](../adr/0003-orchestrator-is-a-scheduler-not-an-authority.md)'s
  explicit rejection of that shape.
