# Orchestrator

## Purpose

Sequences when Research, the Opportunistic Identifier, and the PM run.
Nothing else. See
[ADR 0003](../adr/0003-orchestrator-is-a-scheduler-not-an-authority.md) for
why it deliberately has no data access and no authority over any agent's
output.

## Inputs

- A schedule config, changed only through code review. Defaults:
  - Research daily at 08:30 ET, with an optional intraday interval that is off by default;
  - the Opportunistic Identifier every 60 minutes, 10:00–15:00 ET;
  - the PM's morning session at 10:00 ET;
  - event-driven PM runs at least 30 minutes apart, 5 minutes after the newest report, and
    none from 15:30 ET, or 30 minutes before an early close
    ([ADR 0011](../adr/0011-event-driven-portfolio-manager-runs.md)).

  The config can't loosen those rules. News-triggered Research is decided in the Research
  feature.
- Its own run records, so a restart knows what already ran today.
- The time the most recent report was written, and nothing else about reports.
  It uses this to trigger an event-driven PM run when a new report has arrived
  since the PM last ran.
- The exchange calendar, to skip days the market is closed — checked next to
  the schedule it guards, same pattern as `trading-bot`.
- `system_state.trading_paused` — the one piece of state it reads, solely to
  decide whether to invoke the PM this cycle.

## Outputs

Each agent started as its own process, `python -m trading_agent.<agent>`, with
a timeout and only its own variables: every name must start with that agent's
prefix ([ADR 0015](../adr/0015-orchestrator-starts-agents-with-their-own-credentials.md)).

One run record per run or skipped slot, with why it was due and its outcome
(`orchestrator_runs`). The record is written before the agent starts, so a slot
is claimed only once a day. These are the orchestrator's only database writes.

## Edge cases

- **`trading_paused` is set**: skip the PM invocation entirely for the
  duration of the pause; Research and the Opportunistic Identifier continue
  running on their own schedules regardless (they only produce reports, so
  pausing trading doesn't need to pause them).
- **An agent invocation fails or times out**: log and record the failure, and
  continue the schedule. One agent's failure must not block another's scheduled
  run, and must not silently retry into a runaway loop. A timed-out agent is
  stopped along with anything it started. A failed PM run counts toward the
  30-minute spacing, but its reports stay unconsidered, so the next allowed run
  retries them.
- **The morning session while paused**: it is recorded as skipped. After
  resuming, the event-driven rule picks up the reports it would have considered.
- **The pause flag can't be read**: no PM run. The system fails closed.
- **The orchestrator stops or is redeployed**: it stops every running agent
  first. After a crash, its next start stops any agent still left running before
  marking that run interrupted.
- **An agent exits but leaves processes behind**: they are stopped, so nothing
  of a run outlives it.
- **A report is dated in the future**: it doesn't count until its date, so it
  can't hide the real reports written after it.
- **Process restart mid-day**: re-derive today's schedule from the exchange
  calendar and current time rather than trusting any in-memory state from
  before the restart, same as `trading-bot`'s scheduler. The time of the PM's
  last run is also re-derived after a restart, not held only in memory, so a
  restart can neither skip nor double-fire an event-driven run. A Research run
  or morning session missed because the orchestrator was down runs once, Research
  first, if still before the cutoff. Missed Identifier slots are never
  backfilled.
- **Several reports arrive within 30 minutes of the last PM run**: one PM run
  once the 30 minutes are up, covering all of them.
- **A new report arrives after 15:30 ET** (or within 30 minutes of an early
  close): no PM run. The report expires unused, by design (ADR 0011).

## Interfaces

- Reads only the `trading_paused` column of `system_state`, the exchange
  calendar, and the latest report's creation time, through the one-value view
  `latest_report_time`, never `reports` itself.
- Writes only `orchestrator_runs`. The Assistant and the dashboard read it.
- Details: [`specs/005-orchestrator`](../../specs/005-orchestrator/spec.md),
  including its Clarifications.
- Never reads report contents, `decisions`, `risk_verdicts`, `orders`, or
  `positions`. Never makes a model call itself.

## Non-goals

- Does not review, approve, or alter any agent's output.
- Is not a supervisor and has no authority any agent must defer to — see
  [ADR 0003](../adr/0003-orchestrator-is-a-scheduler-not-an-authority.md)'s
  explicit rejection of that shape.
