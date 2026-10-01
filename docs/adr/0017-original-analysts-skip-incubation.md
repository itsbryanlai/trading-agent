# 0017. The original analysts skip incubation

Status: accepted

## Context

`docs/policy/agent-management.md` says a new analyst agent writes reports for an
incubation period, recommended six months, before the Portfolio Manager reads them.
It defines an analyst as "anything feeding the PM the way Research and the
Opportunistic Identifier do". It doesn't say whether Research and the Opportunistic
Identifier themselves are covered.

They are the original roster ([0001](0001-agent-roster.md)) and the PM's only inputs.
The PM doesn't originate ideas, so if both incubate, the system makes no decisions
for six months. Paper trading already makes running the system the way it gets
observed (Constitution VI).

## Decision

1. **Research and the Opportunistic Identifier are not incubated.** The PM reads
   their reports as soon as each agent is enabled.
2. **Incubation applies to every analyst added after them.** The policy is
   otherwise unchanged.
3. **Their track record still accumulates** through the journal's per-agent
   attribution (`decision_reports`), exactly as it would during incubation.

## Alternatives considered

- **Incubate both.** Rejected: the PM would have no input, so there would be no
  decisions to observe for six months, on an account that is paper only.
- **Incubate one of them**, so the other is measured first. Not chosen: their
  reports are already attributed separately, and the owner can disable either
  agent in `config/schedule.yaml`.

## Consequences

- An agent with no track record influences decisions from day one. The
  safeguards are the Risk Gate's limits, the daily-loss breaker, the manual
  pause, and the account being paper only.
- The policy gains a one-line pointer to this ADR, so its rule reads unambiguously.
