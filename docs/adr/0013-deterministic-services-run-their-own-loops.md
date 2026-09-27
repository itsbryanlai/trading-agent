# 0013. Execution and the Risk Gate's trigger evaluation run their own loops, in separate processes

Status: accepted

## Context

[0003](0003-orchestrator-is-a-scheduler-not-an-authority.md) made the orchestrator the one
scheduler for the LLM agents (Research, the Opportunistic Identifier, the Portfolio Manager). Among
its rejected alternatives was "each agent's own process manages its own schedule", because
independent schedulers can drift from each other and from the exchange calendar.

Execution (`specs/003-execution`) has duties no agent run triggers:

- every ~minute: sync fills and submit new approvals;
- every 30 minutes: the stop-loss monitor ([0010](0010-stop-loss-monitor-and-universe-reference-data.md));
- before each open: the account snapshot.

Stop-loss triggers also need a Risk Gate evaluation. An adversarial review of Execution's design
found that hosting that evaluation inside Execution's process put the gate's database credential
next to the broker credential. The separation between them was then enforced only by code, which
Constitution Principle III says is no boundary at all. The owner chose to have the gate evaluate
triggers in its own process (`specs/003-execution` Clarifications, 2026-09-28).

Two processes that must stay separate for credential reasons can't both be hosted by one
orchestrator process without undoing that separation.

## Decision

1. **Execution runs its own loop in its own process** (`python -m trading_agent.execution`). It
   holds only the broker keys and the `ta_execution` database login. It ticks every 60 seconds.
   Each tick asks the exchange calendar what is due, and finds new approvals in the database rather
   than being handed them.
2. **The Risk Gate's trigger evaluation runs its own loop in its own process**
   (`python -m trading_agent.risk`). It holds only the `ta_risk_gate` login and evaluates new
   stop-loss triggers every 60 seconds. A recorded trigger is the whole hand-off from Execution.
3. **The orchestrator keeps 0003's role for the LLM agents** and does not schedule either loop. The
   Portfolio Manager's gate evaluations still happen in the PM runner's process, as before.
4. **Drift is contained by construction**: both loops take every time judgement from the same
   `trading_agent.risk.calendar` module (XNYS, early closes included), and both are idempotent. A
   missed or doubled tick repeats harmless work; it never trades twice (`specs/003-execution`
   research E5).
5. **Each process exits on a lost database connection** rather than logging failures forever, so
   the platform's restart policy recovers it. Execution also logs an error when a trigger from today
   has gone unevaluated for more than about 5 minutes, so a missing gate process is visible.

This qualifies 0003's rejected alternative for these two deterministic services only. It doesn't
supersede 0003: every LLM agent is still scheduled by the orchestrator.

## Alternatives considered

- **The orchestrator hosts both loops.** It fits 0003 literally, but the gate's credential and the
  broker credential would share the orchestrator's process, which is the exact problem the review
  found.
- **The orchestrator spawns and supervises separate processes.** It keeps the separation, but gives
  the orchestrator process-management duties and a dependency on both components' credentials
  being present in its environment to pass on. The platform already supervises processes.
- **Evaluate triggers from Execution's process with an injected callable.** This was the first
  design, rejected for Principle III as described above.

## Consequences

- Deployment gains two long-running processes: Execution, and the gate's trigger runner. Both live
  in the worker service or alongside it, each with only its own environment variables. The repo has
  no deployment configuration yet; `specs/003-execution` documents both commands and their
  environment variables, and whichever feature first writes the Railway configuration must start
  both processes.
- A stop-loss exit takes up to about two extra minutes: trigger, then gate pass, then Execution's
  next tick. That is small against the 30-minute monitor interval.
- If the gate process is down, stop-loss exits wait. Execution's "unevaluated trigger" error is the
  signal; the owner-facing alert comes later with the Assistant or the dashboard.
- A future deterministic service with its own cadence (e.g. the reference-data job) can follow the
  same pattern by citing this ADR, rather than reopening 0003.
