# Architecture overview

A multi-agent day-trading system for US equities: two analyst agents propose,
one agent decides, two deterministic services vet and execute, and a fourth
agent answers questions about the whole thing. Every decision behind this
shape has a corresponding ADR in [`docs/adr/`](../adr/README.md); this
document is the map, not the rationale.

## Components

```
                    ┌─────────────┐
   news, filings →  │  Research   │──┐
                    └─────────────┘  │
                                     │  reports (DB)
   market scan   →  ┌─────────────┐  │
                    │Opportunistic│──┤
                    │ Identifier  │  │
                    └─────────────┘  │
                                     ▼
                            ┌─────────────────┐
                            │ Portfolio        │
                            │ Manager (PM)     │  decides direction + size
                            └────────┬─────────┘
                                     │ decision (DB)
                                     ▼
                            ┌─────────────────┐
                            │   Risk Gate      │  deterministic, config-driven
                            │  (config/risk.   │
                            │      yaml)       │
                            └────────┬─────────┘
                             approved verdict
                                     ▼
                            ┌─────────────────┐
                            │   Execution      │  deterministic, holds broker
                            │                  │  credentials, only writer of
                            └────────┬─────────┘  the orders table
                                     ▼
                              Alpaca (paper)

   ┌─────────────┐         reads everything, writes nothing
   │  Assistant  │◄──────────────────────────────────────────┐
   └──────┬──────┘                                            │
          │ Telegram (single authorized chat)          Postgres (shared,
          ▼                                             role-scoped grants)
        user
```

An **orchestrator** (not pictured above — it has no place in the data flow)
sequences when Research, the Opportunistic Identifier, and the PM run, and has no
authority over what they conclude
([ADR 0003](../adr/0003-orchestrator-is-a-scheduler-not-an-authority.md)). It is its
own process that starts each agent as a child process, passing it only that agent's
own variables ([ADR 0015](../adr/0015-orchestrator-starts-agents-with-their-own-credentials.md)),
and it makes no model call. Its database role reads only the latest report's time
and the pause flag, and writes only its own run records
([ADR 0011](../adr/0011-event-driven-portfolio-manager-runs.md),
[`specs/005-orchestrator`](../../specs/005-orchestrator/spec.md)).

## Agents (LLM, judgment)

| Agent | Reads | Writes | Cadence |
|---|---|---|---|
| Research | Finnhub news (general + an owner watchlist), own credential; model per [ADR 0018](../adr/0018-qwen-as-a-model-provider.md) | `reports` (own rows) | daily at 08:30 ET ([`specs/007-research-agent`](../../specs/007-research-agent/spec.md)) |
| Opportunistic Identifier | market data and fundamentals (read-only Finnhub key, [ADR 0016](../adr/0016-market-data-for-the-llm-agents.md)) | `reports` (own rows) | intraday polling |
| Portfolio Manager | both agents' open reports, portfolio state, live quote (read-only Finnhub key, [ADR 0016](../adr/0016-market-data-for-the-llm-agents.md)), journal | `decisions` | morning session + event-driven on new reports, ≥30 min apart, none after 15:30 ET ([ADR 0011](../adr/0011-event-driven-portfolio-manager-runs.md)) |
| Assistant | everything | nothing | on-demand (Telegram) |

Full behavior, inputs/outputs, and edge cases for each are in
[`docs/specs/`](../specs/README.md).

## Deterministic services (no model call, credential-isolated)

| Service | Reads | Writes | Holds |
|---|---|---|---|
| Risk Gate | a `decisions` row, `config/risk.yaml`, market-open flag from caller | `risk_verdicts` | nothing (pure function, no credentials) |
| Execution | an approved `risk_verdicts` row | `orders` | the only broker (Alpaca) credentials in the system |
| Reference-data job | `reference_candidate_symbols` (held and recently named symbols), a seed list, Finnhub | `instrument_reference`, insert-only | a read-only Finnhub key; cannot trade |

Execution, the Risk Gate's stop-loss trigger runner and the reference-data job each run
their own loop in their own process ([ADR 0013](../adr/0013-deterministic-services-run-their-own-loops.md)).
The reference-data job records the universe facts the gate checks every buy against, once per
symbol per trading day, from 08:00 ET until the close; a symbol without today's row can't be
bought ([ADR 0010](../adr/0010-stop-loss-monitor-and-universe-reference-data.md) §3,
[`docs/specs/reference-data.md`](../specs/reference-data.md)).

## Data

One shared Postgres instance is the entire knowledge base — see
[`docs/specs/data-model.md`](../specs/data-model.md) for the schema. Every
component connects with its own database role; broad read access is the
default, write access is scoped to the table(s) that component owns
([ADR 0004](../adr/0004-shared-postgres-role-scoped-credentials.md)).

## Safety properties

- **No approval loop, one automatic hard stop.** No PM decision needs a human
  sign-off; a 20% daily-loss breaker halts new orders for the rest of the day
  and clears itself next session
  ([ADR 0006](../adr/0006-autonomous-operation-with-daily-loss-breaker.md)).
- **Every order passes the Risk Gate.** Nothing else in the system can
  construct one.
- **Only Execution can reach the broker.** No LLM agent's output has a path
  to a live order — the PM's decision is a proposal until the Risk Gate and
  Execution act on it.
- **Paper trading only**, same as `trading-bot` — this design does not itself
  change that; see the relevant spec/ADR before it does.

## Deployment shape (borrowed pattern, not shared code, from `trading-bot`)

- Railway worker service running the orchestrator + agents, Nixpacks build
- Railway Postgres service for the shared knowledge base
- A separate Railway web service for the dashboard UI (FastAPI + Jinja2,
  matching `trading-bot`'s own status-view deployment —
  [ADR 0008](../adr/0008-dashboard-stack-and-research-provider.md)), granted
  a read-only DB role like the Assistant's, plus write access to the single
  `trading_paused` toggle
- Alpaca paper endpoint for order execution and Execution's own market data; no agent holds an Alpaca credential, and the agents' quotes come from read-only Finnhub keys ([ADR 0016](../adr/0016-market-data-for-the-llm-agents.md))
- Credentials never committed: Alpaca keys, model-provider keys (Anthropic or Qwen, per agent — [ADR 0018](../adr/0018-qwen-as-a-model-provider.md)), Finnhub keys, Telegram bot
  token/chat ID, each scoped to the one component that needs them

## Open items

Anything not yet settled by an ADR or a spec belongs here until it is:

- Alpha Vantage's `NEWS_SENTIMENT` as a secondary signal for Research,
  alongside its primary Finnhub feed
  ([ADR 0008](../adr/0008-dashboard-stack-and-research-provider.md)) — a
  documented candidate addition, not yet wired up
- A `CONTEXT.md` domain glossary for this project, mirroring `trading-bot`'s
  (see `docs/policy/agent-management.md`'s naming discipline section) — not
  yet written
