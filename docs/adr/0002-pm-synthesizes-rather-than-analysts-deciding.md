# 0002. The Portfolio Manager decides; analyst agents only propose

Status: accepted

## Context

If Research or the Opportunistic Identifier could each place trades directly
on their own conviction, an agent would be judging its own idea — the same
model call that sourced a thesis would also decide whether to act on it, with
no independent check on its own bias. Two agents converging on the same name
is also a meaningful signal that a single-analyst system can't produce at all.

## Decision

Research and the Opportunistic Identifier only ever emit reports (direction,
conviction, sizing suggestion, sources, rationale — see
`docs/specs/data-model.md`). Neither can place a trade. The Portfolio Manager
is the only agent that decides: it reads every open (unexpired) report from
both analysts, re-derives portfolio state and a live quote itself rather than
trusting numbers in a report, and independently decides direction and size.

Convergence between the two analysts is treated as a positive signal the PM
may weigh, not as a rule that mechanically sums their suggested sizes — the PM
does not buy 2x just because both agents recommended a name.

## Alternatives considered

- Each analyst agent places its own trades within its own sub-budget. Rejected:
  reintroduces the self-bias problem this ADR exists to avoid, and requires a
  budget-split and same-symbol collision policy (see the rejected shape of
  `docs/adr/0001`'s Q8 discussion) that the synthesis model makes unnecessary.
- A rules-based aggregator (e.g. "average the two suggested sizes") instead of
  an LLM PM. Rejected: sizing a trade well depends on portfolio context (current
  exposure, cash, correlation to existing positions) that a fixed formula
  can't weigh as well as a model reading the same context the analysts read.

## Consequences

- The PM is a single point of failure for judgment quality — its own
  reasoning is worth auditing closely (see per-decision logging in
  `docs/specs/portfolio-manager-agent.md`).
- Per-agent performance attribution (which analyst's ideas would have done
  well on their own) requires the PM to log which report(s) it drew on for
  every decision — this is load-bearing for the project's own stated goal of
  watching how each agent performs over time.
- A report is a proposal with a shelf life (expires at that trading day's
  close), not a standing instruction — the PM re-evaluates fresh each session
  rather than accumulating stale conviction.
