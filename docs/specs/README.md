# Specs

One file per module or feature, describing behavior — not implementation. A spec should let someone build the thing without you in the room, and let a reviewer check the build against it afterward.

## What goes in a spec

- Purpose: what this module is responsible for, and what it explicitly isn't
- Inputs and outputs, with types/shapes where they matter
- Edge cases and error behavior (bad data, missing data, downstream failure)
- Interfaces to other modules (what it calls, what calls it)
- Non-goals: what's deliberately out of scope, to head off scope creep later

## What doesn't

Function signatures, class hierarchies, pseudocode, library choices — those belong to the implementation phase, not the spec. If a spec needs a library choice to be legible, note the constraint that drives it and let implementation pick.

## Index

| Spec | Covers |
|---|---|
| [`data-model.md`](data-model.md) | Shared database schema and per-table write ownership |
| [`research-agent.md`](research-agent.md) | Sentiment/news analyst |
| [`opportunistic-identifier-agent.md`](opportunistic-identifier-agent.md) | Undervaluation scanner analyst |
| [`portfolio-manager-agent.md`](portfolio-manager-agent.md) | The decision-maker |
| [`assistant-agent.md`](assistant-agent.md) | Read-only Telegram Q&A |
| [`risk-gate.md`](risk-gate.md) | Deterministic trade validation |
| [`execution.md`](execution.md) | Deterministic order submission |
| [`orchestrator.md`](orchestrator.md) | Scheduling, no authority |
| [`ui-dashboard.md`](ui-dashboard.md) | Monitoring surface |
