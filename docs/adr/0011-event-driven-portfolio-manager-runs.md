# 0011. Portfolio Manager: a morning session plus event-driven intraday runs

Status: accepted

## Context

The Portfolio Manager was designed to run once per trading day, mid-morning.
That is one fixed session, with event-triggered runs explicitly deferred "until
the daily cadence is observed to miss time-sensitive opportunities"
(`docs/specs/portfolio-manager-agent.md`; the cadence
[0003](0003-orchestrator-is-a-scheduler-not-an-authority.md) sequences).

Specifying the Risk Gate showed it would miss them by design. The Opportunistic
Identifier scans all day, but reports expire at that day's close. Anything it
flags after the morning session expires before the next one and is never acted
on.

On a real trading floor, a PM's day has two halves. A morning meeting before the
open sets the day's plan. Intraday decisions are event-driven: the PM steps back
in when something material happens, not on a timer. Risk is watched
continuously by a separate function, which here is the Risk Gate and
Execution's stop-loss monitor.

## Decision

The Portfolio Manager runs:

- **A morning session** at a fixed time after the open, deciding on everything
  open, chiefly Research's pre-open reports. This is the "morning meeting".
- **Event-driven runs** during market hours whenever at least one *new* report
  (from either analyst) has been written since the PM's last run, subject to:
  - **At least 30 minutes between PM runs.** A burst of reports is handled in
    one run, not one run per report.
  - **No run after 15:30 ET**, so approved buys have time to fill and the PM
    never buys into the close.

Each run considers every open report, not only the new one, and decides from
fresh portfolio state. The orchestrator detects "a new report since the last run"
and triggers the run. This stays within [0003](0003-orchestrator-is-a-scheduler-not-an-authority.md):
the orchestrator only sequences, and it needs read access to report timestamps,
not their contents.

## Alternatives considered

- **Keep one session a day** and move the Opportunistic Identifier's scans to
  pre-open only. Rejected: it drops the intraday opportunity-finding that is the
  Opportunistic Identifier's reason to exist.
- **A fixed intraday schedule** (e.g. every 30 or 60 minutes). Rejected: it pays
  for model calls on runs with nothing new to consider, and is less like how a PM
  actually works.
- **A PM run per report, no spacing.** Rejected: a busy news period could
  trigger many runs in minutes, multiplying cost and churn for no better decisions.

## Consequences

- Re-running the PM is safe because of decisions already made. Size is a target
  weight, so a met target produces no order and nothing is ever bought twice
  (`specs/002-risk-gate`). The daily order cap bounds churn. Approvals are valid
  only on their own trading day.
- Model-call cost rises on busy days, from one PM run to several; it is bounded
  by the 30-minute spacing and the 15:30 cutoff (at most about 13 runs a day).
- The orchestrator gains a read of report metadata (creation times). That is a
  new database grant for `ta_orchestrator`, added in the orchestrator feature's
  plan, not now.
- An Opportunistic Identifier report written after 15:30 still expires unused.
  That is accepted: the system doesn't open new positions into the close.
