# Architecture Decision Records

One file per decision worth remembering the *why* of: `NNNN-short-title.md`, numbered sequentially (`0001-`, `0002-`, ...).

An ADR is accepted once written — don't edit an accepted ADR to change its decision. If a later decision reverses or replaces it, write a new ADR and mark the old one superseded.

## Template

```markdown
# NNNN. Title

Status: proposed | accepted | superseded by NNNN

## Context
What problem or question forced this decision.

## Decision
What we chose.

## Alternatives considered
What else we looked at, and why we didn't pick it.

## Consequences
What this makes easier, harder, or locks in going forward.
```

## Index

| # | Title |
|---|---|
| [0001](0001-agent-roster.md) | Agent roster: three analysts-and-decider agents, plus a read-only Assistant |
| [0002](0002-pm-synthesizes-rather-than-analysts-deciding.md) | The Portfolio Manager decides; analyst agents only propose |
| [0003](0003-orchestrator-is-a-scheduler-not-an-authority.md) | The orchestrator sequences agent runs; it has no decision authority |
| [0004](0004-shared-postgres-role-scoped-credentials.md) | One shared Postgres database, role-scoped per component |
| [0005](0005-risk-gate-and-execution-are-deterministic.md) | Risk Gate and Execution are deterministic, not LLM agents |
| [0006](0006-autonomous-operation-with-daily-loss-breaker.md) | Fully autonomous operation; a daily-loss breaker is the sole automatic hard stop |
| [0007](0007-assistant-is-separate-read-only-telegram.md) | The Assistant is a separate agent, read-only, reachable over Telegram |
| [0008](0008-dashboard-stack-and-research-provider.md) | Dashboard stack and Research's news provider |
| [0009](0009-implementation-phase-started.md) | Implementation phase started |
| [0010](0010-stop-loss-monitor-and-universe-reference-data.md) | Stop-loss monitoring, universe reference data, and the daily baseline |
| [0011](0011-event-driven-portfolio-manager-runs.md) | Portfolio Manager: a morning session plus event-driven intraday runs |
| [0012](0012-order-identifier-per-verdict.md) | Order identifiers are per verdict, not per symbol, side and day |
| [0013](0013-deterministic-services-run-their-own-loops.md) | Execution and the Risk Gate's trigger evaluation run their own loops, in separate processes |
| [0014](0014-fresh-confirmed-stop-loss-triggers-and-intraday-equity.md) | Stop-loss triggers must be fresh and confirmed; equity is recorded every stop-loss window |
| [0015](0015-orchestrator-starts-agents-with-their-own-credentials.md) | The orchestrator starts each agent as a process, passing it only its own credentials |
| [0016](0016-market-data-for-the-llm-agents.md) | Market data for the LLM agents comes from read-only Finnhub keys |
| [0017](0017-original-analysts-skip-incubation.md) | The original analysts skip incubation |
| [0018](0018-qwen-as-a-model-provider.md) | Qwen is an approved model provider alongside Anthropic |
