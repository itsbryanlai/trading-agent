# Agent management policy

The rules for creating, granting permissions to, changing, and retiring an
agent or deterministic service in this system. Where a rule here conflicts
with convenience during implementation, this document wins — it exists
specifically so autonomy (no per-trade approval,
[ADR 0006](../adr/0006-autonomous-operation-with-daily-loss-breaker.md))
doesn't quietly become a lack of control.

## Vocabulary: agent vs. service

An **agent** has judgment — it makes a model call and its output can vary
run to run. A **service** is deterministic — same input, same output, no
model call, testable with fixed fixtures. This line is load-bearing
([ADR 0001](../adr/0001-agent-roster.md)): the Risk Gate and Execution are
services precisely because the one thing standing between a decision and
real money must not inherit a model's variability
([ADR 0005](../adr/0005-risk-gate-and-execution-are-deterministic.md)).

Every new addition to this system must be classified as one or the other
before anything else about it is decided. "It's mostly deterministic but
calls a model for one edge case" is not a valid classification — it's an
agent, and everything below about agents applies to it.

## Adding a new agent

In order, before a line of implementation code is written:

1. **Write the spec** (`docs/specs/<name>-agent.md`), following the template
   in `docs/specs/README.md`: purpose, inputs, outputs, edge cases,
   interfaces, non-goals. The non-goals section is not boilerplate — it is
   where scope creep gets stopped in writing, before it happens in code.
2. **Write an ADR** if the agent changes the shape of the system (a new role
   no existing agent covers, a new data flow, a new external dependency) —
   not required for a parameter change to an existing agent's behavior.
3. **State its database role explicitly**: what it reads (default: broad),
   what it writes (default: nothing until justified — see Least privilege
   below), and whether it needs any credential beyond the shared database
   (a new agent needing broker credentials should be treated as
   exceptional and questioned hard — see Least privilege).
4. **State its blast radius**: if this agent is wrong, hallucinates, or is
   prompt-injected via data it reads (news content, for instance, is
   attacker-reachable text), what is the worst it can do? "It can write a
   bad report the PM might read" is an acceptable answer. "It can place an
   order" is not, for any agent except Execution, and Execution is a service
   with no model call — no *agent* should ever be able to answer "it can
   place an order."

Only after all four are written and reviewed does implementation start.

## Least privilege, enforced at the database

Every agent and service connects with its own database role
([ADR 0004](../adr/0004-shared-postgres-role-scoped-credentials.md)). When
granting a new role:

- Start from **zero write grants** and add only the specific table(s) the
  spec's Outputs section names. A role that can write to a table its spec
  never mentions is a bug in the grant, not a convenience.
- Read access is broad by default, but re-check it against the spec's
  Non-goals — if a spec says an agent doesn't reason about portfolio state,
  it should not have a reason to be granted read access it doesn't use
  either; unused grants are dead surface, not neutral.
- **Broker credentials are granted to exactly one role: Execution.** Any
  proposal to grant broker credentials to a second component is itself a
  design decision requiring a new ADR, not a config change.
- No agent's role may write to `config/risk.yaml` or any table that
  influences the Risk Gate's evaluation — see Changing risk limits, below.

A permission a role doesn't need should not exist "for later." Grant it when
a spec justifies it, not before.

## Incubation before an agent can influence real decisions

A new analyst agent (anything feeding the PM the way Research and the
Opportunistic Identifier do) does not start influencing PM decisions on day
one. It runs for a minimum incubation period — recommended six months,
matching `trading-bot`'s own incubation window for a rule strategy — writing
`reports` rows normally, but flagged (e.g. a `provisional` status or a
separate table) so the PM does not yet read them as input. Attribution and
journal tracking run exactly as they would for a live agent, so its track
record accumulates before it can move money.

Promotion out of incubation — the point where the PM starts reading its
reports — is always a deliberate, logged action by the owner, informed by
the incubation record. It is never automatic, the same way `trading-bot`
never auto-promotes a backtested strategy to live.

## Changing risk limits

`config/risk.yaml` is editable by design
([ADR 0005](../adr/0005-risk-gate-and-execution-are-deterministic.md)), which
means the review discipline around it matters more, not less:

- A change to `config/risk.yaml` is reviewed the same way a code change to
  the Risk Gate itself would be — it is not a config toggle exempt from
  scrutiny just because it's a YAML file.
- No agent may propose, suggest committing, or programmatically write a
  change to this file. A model noticing "the daily-loss halt keeps
  triggering" and surfacing that in the journal or to the Assistant is fine
  and expected; a model editing the file in response is not — that decision
  stays with the owner.
- Loosening a limit (raising `max_position_pct`, `daily_loss_halt_pct`, or
  `max_orders_per_day`; lowering `cash_reserve_pct` or the universe's
  liquidity/market-cap floors) is worth a deliberate pause before merging —
  tightening a limit is not held to the same bar.

## Retiring an agent

- Revoke its database role's grants first, remove its cadence from the
  orchestrator's schedule second — in that order, so a scheduled run can't
  execute against a role that no longer has the access its code assumes.
- Historical `reports` or `decisions` rows it wrote are never deleted — they
  remain part of the journal's attribution history even after the agent
  that wrote them is gone.
- Update the relevant spec's status to retired rather than deleting the
  file — the spec is part of the historical record of what the system used
  to do, same reasoning as ADRs never being edited after acceptance.

## Naming discipline

One term, one meaning, used consistently across specs, ADRs, code, and the
UI — the same discipline `trading-bot`'s `CONTEXT.md` glossary enforces
there. As this system accumulates its own domain vocabulary (beyond the
roles already named in `docs/specs/`), it should get its own glossary file
rather than letting each spec redefine terms locally.
