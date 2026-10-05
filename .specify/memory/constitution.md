<!--
Sync Impact Report
Version change: 1.1.0 → 1.1.1 (PATCH: wording of one deployment bullet, no behavioral change)
Modified sections:
  - Technology & Deployment Constraints: the Railway bullet now names infrastructure
    as code (`.railway/railway.ts`) in place of `railway.json` / `railway.web.json`
    (Railway deprecates Config as Code), and one worker service per process in place
    of a single worker service for the orchestrator and agents (ADR 0021).
Modified principles: none.
Added / removed sections: none.
Templates requiring follow-up: none. The plan template's Constitution Check reads
  this file directly.
Deferred TODOs: none.
-->

<!--
Sync Impact Report
Version change: 1.0.0 → 1.1.0 (MINOR: materially expanded guidance)
Modified sections:
  - Technology & Deployment Constraints: LLM agents may use Anthropic (`anthropic`
    SDK) or Qwen (QwenCloud's OpenAI-compatible API), chosen per agent by
    configuration (ADR 0018); the Qwen (DashScope) key added to the credentials list.
Modified principles: none.
Added / removed sections: none.
Templates requiring follow-up: none. The plan template's Constitution Check reads
  this file directly.
Deferred TODOs: none.
-->

# trading-agent Constitution

## Core Principles

### I. Deterministic Trade Path (NON-NEGOTIABLE)
The Risk Gate and Execution MUST remain deterministic: no model call, no
non-deterministic behavior, and no data source beyond what is explicitly
passed to them (the Risk Gate reads no environment or network itself —
whether the market is open arrives as a boolean from its caller). Every
order MUST pass through the Risk Gate before submission; no other component
may construct an order. Execution is the only component in the system
permitted to hold broker credentials, and it MUST re-derive position and
cash ceilings itself before submitting rather than trusting the verdict it
was handed. No LLM agent's output may have a path to the broker.
Rationale: with no per-trade human approval (Principle IV), the one thing
standing between a model's output and real money must be exactly as
predictable and testable as a pure function — fixed input, fixed output,
verifiable with fixture-based tests and no flakiness.

### II. Analysts Propose, the Portfolio Manager Decides
Research and the Opportunistic Identifier MUST only emit reports (direction,
conviction, suggested size, structured sources, rationale); neither may
place a trade or otherwise act on its own conviction. The Portfolio Manager
is the sole decision-maker: it MUST re-derive portfolio state and a live
quote itself rather than trust numbers in a report, and MUST NOT read
`config/risk.yaml` — its judgment must not be shaped by what it knows will
pass the gate. Convergence between the two analysts on the same symbol MAY
be weighed as a positive signal but MUST NOT mechanically increase size
(e.g., never a simple sum of both agents' suggestions). Every Portfolio
Manager decision MUST record which report(s) it drew on.
Rationale: an agent judging its own sourced idea has no independent check on
its own bias; separating "found this" from "decided this" is what makes the
system's per-agent performance attribution meaningful.

### III. Least-Privilege Credentials, Enforced at the Database
Every agent and service MUST connect to the shared Postgres knowledge base
with its own database role. Read access is broad by default; write access
MUST be scoped to only the table(s) that component's spec names as its
output. Broker credentials MUST be granted to exactly one role: Execution.
Granting broker credentials to any second component, or granting a role a
write beyond what its spec justifies, requires a new spec (and, if it
changes the system's shape, a new ADR) before the grant is made — never as
a standalone permissions change.
Rationale: a permission boundary enforced only by application code choosing
to behave is not a boundary; the database grant is the actual enforcement
point, so a bug or a compromised agent is contained to what its role can
touch.

### IV. Autonomous Operation, One Automatic Hard Stop
No Portfolio Manager decision requires human approval before reaching the
Risk Gate. The sole automatic hard stop is the daily-loss breaker: if
account equity falls 20% below that trading day's starting equity, new
order submission halts for the remainder of the day. The breaker MUST NOT
force-liquidate open positions — each position's own stop-loss continues to
apply — and MUST clear automatically at the start of the next trading day
with no manual step required. Manual pause/resume of trading MUST exist as
a single plain toggle outside any agent's control (not reachable through
the Assistant or any natural-language interface).
Rationale: the system's purpose is observing how the agents perform over
time with minimal intervention; a per-trade approval loop would defeat that,
but unattended operation still needs one automatic backstop for a genuinely
bad day.

### V. Spec-and-ADR-First Change Control
A behavior change to any agent or service MUST be reflected in its spec
under `docs/specs/` before or alongside the implementation change — a spec
describes behavior, not implementation detail, and remains the source of
truth read before touching that component's code. A change to the system's
shape (a new role, a new data flow, a new external dependency, a new
credential category) MUST get an ADR under `docs/adr/` first. An accepted
ADR is never edited to change its decision; a reversal is a new ADR marking
the old one superseded. Changes to `config/risk.yaml` MUST be reviewed with
the same scrutiny as a code change to the Risk Gate itself, and no agent may
author or suggest committing a change to that file.
Rationale: editability of risk limits and the ease of adding new agents are
deliberate design choices; this principle is what keeps that flexibility
from becoming an unreviewed path to loosening safety controls.

### VI. Paper Trading Only, US Equities
The system MUST refuse to start against any non-paper broker endpoint. The
eligible trading universe is US-listed common equities only, subject to the
floors and exclusions in `config/risk.yaml` (market capitalization, average
daily dollar volume, minimum share price; no OTC listings, no leveraged or
inverse ETFs, no options). The Risk Gate MUST re-check universe eligibility
independently rather than trusting that upstream agent filtering held.
Rationale: paper-only is the hard boundary between this project and real
financial risk; universe floors keep the Opportunistic Identifier's broad
scanning out of manipulation-prone or illiquid names.

### VII. The Assistant and Dashboard Are Read-Only
The Assistant (Telegram-facing) and the dashboard UI MUST have read access
to every table in the shared knowledge base and MUST NOT be granted any
write access beyond the dashboard's single `trading_paused` toggle. Neither
may trigger, influence, approve, or veto a trade through any path,
including natural-language interpretation of a request to do so.
Rationale: a monitoring and Q&A surface that can also act is a second,
unaudited path into the trading pipeline; keeping it strictly read-only
bounds the blast radius of a wrong or misled response to "misleading the
user," never "moving money."

## Technology & Deployment Constraints

- Python throughout, matching the precedent established by the sibling
  `trading-bot` project: FastAPI + Jinja2 (server-rendered, no frontend
  build step) for the dashboard, `psycopg` for Postgres access, an
  in-process scheduler for the orchestrator. Each LLM agent uses an approved
  model provider, chosen per agent in version-controlled configuration:
  Anthropic through the `anthropic` SDK, or Qwen through QwenCloud's
  OpenAI-compatible API ([ADR 0018](../../docs/adr/0018-qwen-as-a-model-provider.md)).
  A further provider requires its own ADR.
- Postgres is the only durable store. No component may rely on container
  filesystem state surviving a redeploy; anything worth keeping is a
  database row.
- Deployment targets Railway, described as infrastructure as code
  (`.railway/railway.ts`; Railway's `railway.json` Config as Code is
  deprecated): one worker service per process (the orchestrator with its
  agents, the Risk Gate's loop, the reference-data job, Execution), a
  separate web service for the dashboard, and a managed Postgres service
  ([ADR 0021](../../docs/adr/0021-railway-deployment-as-code-observe-only-first.md)).
- Credentials are never committed. Each component's credential (Alpaca,
  Anthropic, Qwen (DashScope), Finnhub, Telegram bot token, database role connection string)
  is distinct and scoped to that component; `.env.example` is kept current
  as new credentials are introduced.

## Development Workflow

- New agents and services follow `docs/policy/agent-management.md`: spec
  first, ADR if the system's shape changes, least-privilege database role
  granted only after the spec justifies it, and — for a new analyst agent —
  an incubation period before the Portfolio Manager reads its reports.
- Implementation proceeds foundation-first: the shared data model, then the
  Risk Gate and Execution, then the orchestrator, then the analyst and
  decision agents, then the Assistant, then the dashboard — each landed and
  reviewed before the next begins, not implemented in parallel.
- Every logic change is covered by tests before being considered done. The
  Risk Gate and Execution, being deterministic, MUST be tested with fixed
  inputs and fixed expected outputs.
- Changes are small and focused; a change to risk limits, position sizing,
  or order logic is flagged explicitly before being applied, per
  `CLAUDE.md`.
- No agent places a real order, executes a trade, or moves funds — including
  against the paper account — without the user explicitly asking for that
  specific action in the current conversation.

## Governance

This constitution supersedes any conflicting ad hoc practice. `CLAUDE.md`
carries day-to-day operating rules for assistants working in this repo;
this document carries the structural principles those rules must never
contradict, and that every `/speckit-plan` and code review is checked
against.

Amending this constitution requires: a corresponding ADR under `docs/adr/`
explaining the why (the same bar as any other decision that changes the
system's shape), an update to this file with a Sync Impact Report noting
what changed, and a version bump following semantic versioning — MAJOR for
a backward-incompatible principle removal or redefinition, MINOR for a new
principle or materially expanded guidance, PATCH for wording or clarity
fixes with no behavioral change. Compliance with these principles is
reviewed the same way any other code review checks compliance with
`docs/specs/` and `docs/adr/`.

**Version**: 1.1.1 | **Ratified**: 2026-09-26 | **Last Amended**: 2026-10-05
