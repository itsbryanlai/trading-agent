# 0003. The orchestrator sequences agent runs; it has no decision authority

Status: accepted

## Context

Coordinating four agents with different cadences (Research ~daily,
Opportunistic Identifier intraday, PM once daily, Assistant on-demand) needs
*something* deciding when each one runs. The question is whether that
something also gets a say in *what they decide* — a hierarchy — or stays
purely mechanical.

## Decision

The orchestrator is a scheduler: it triggers Research, the Opportunistic
Identifier, and the PM on their configured cadences, and nothing else. It has
no read access to trading data, no LLM call of its own, and cannot alter,
approve, or override any agent's output. No agent has authority over another;
the only thing that can stop a PM decision from becoming an order is the Risk
Gate, which is not part of the orchestrator.

## Alternatives considered

- A supervisor agent that reviews PM decisions before they reach the Risk
  Gate. Rejected: this is just a second decision-maker under another name,
  reintroducing the bias/authority questions ADR 0002 settled, and blurring
  who is accountable for a bad call.
- No orchestrator at all — each agent's own process manages its own schedule.
  Rejected: `trading-bot`'s in-process scheduler pattern (market-calendar
  check next to the schedule it guards) is proven and simpler to reason about
  than N independent schedulers that could drift out of sync with each other
  and with the exchange calendar.

## Consequences

- Adding a new agent means adding one cadence entry to the orchestrator's
  schedule — it never needs new logic to accommodate a new kind of decision.
- The orchestrator can be tested and reasoned about without any knowledge of
  trading logic at all; a bug in it can misfire a schedule but can never
  place, approve, or block a trade.
