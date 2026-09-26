# 0009. Implementation phase started

Status: accepted

## Context

`CLAUDE.md` gates the whole repo to documentation-only until an ADR here
says otherwise. Design is settled: agent roster ([0001](0001-agent-roster.md)),
decision synthesis ([0002](0002-pm-synthesizes-rather-than-analysts-deciding.md)),
orchestration ([0003](0003-orchestrator-is-a-scheduler-not-an-authority.md)),
the shared database and its role grants ([0004](0004-shared-postgres-role-scoped-credentials.md)),
the deterministic Risk Gate and Execution ([0005](0005-risk-gate-and-execution-are-deterministic.md)),
autonomous operation with a daily-loss breaker ([0006](0006-autonomous-operation-with-daily-loss-breaker.md)),
the read-only Assistant ([0007](0007-assistant-is-separate-read-only-telegram.md)),
and the dashboard stack plus Research's data provider ([0008](0008-dashboard-stack-and-research-provider.md))
are all written up, along with per-component specs in `docs/specs/` and the
rules for adding/permissioning/retiring an agent in
`docs/policy/agent-management.md`. Nothing left to settle before writing code.

## Decision

Implementation starts now. `CLAUDE.md`'s docs-only restriction is lifted;
its "Once implementation starts" section is now in effect. Stack: Python,
matching `trading-bot`'s choices — FastAPI + Jinja2 (server-rendered, no
frontend build step) for the dashboard, `psycopg` for Postgres, an
APScheduler-style in-process scheduler for the orchestrator, the `anthropic`
SDK for every LLM agent — reused as a pattern for consistency with an
already-proven deployment, not shared code.

Per `docs/specs/README.md`'s Non-goals ("if you catch yourself writing
function signatures or pseudocode, stop"), that line no longer applies from
this ADR forward — implementation-phase work belongs in code, not in specs.
Existing specs remain the source of truth for *behavior*; they are not
retroactively rewritten into implementation detail.

## Alternatives considered

Not applicable — this ADR records a phase transition, not a design choice
between options.

## Consequences

- `docs/adr/`, `docs/specs/`, and `docs/policy/` stop being the only content
  this repo accepts; application code, `config/risk.yaml` (now an actual
  file, not just a snippet in `docs/specs/risk-gate.md`), and a real
  `.env.example` are all in scope from here on.
- Every rule in `CLAUDE.md`'s "Once implementation starts" section — no
  committed secrets, no order placed without explicit ask, flag changes to
  risk/sizing/order logic, run tests, small focused changes — applies
  starting now, not retroactively to anything before this ADR.
- Future design changes still get an ADR first, same as before
  ([`docs/adr/README.md`](README.md)) — this phase transition does not lower
  that bar, it only adds code alongside the docs.
