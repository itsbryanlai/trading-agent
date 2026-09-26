# 0004. One shared Postgres database, role-scoped per component

Status: accepted

## Context

"All agents share the same knowledge base, but each is limited to the minimum
permission it needs" is the founding permission model for this project. That
needs a concrete mechanism, not just a policy statement — permissions have to
be enforced somewhere a prompt-injected or simply mistaken agent can't argue
its way around.

## Decision

One Postgres instance is the entire knowledge base: analyst reports, PM
decisions, risk verdicts, orders, positions, and the journal all live there.
Every component connects with its own database role:

- Broad **read** access is the default across roles — an agent generally needs
  to see the whole picture to reason well.
- **Write** access is scoped to the table(s) that component owns: Research and
  the Opportunistic Identifier can only insert into the reports table under
  their own `agent` value; the PM can only insert into the decisions table;
  Execution is the only role that can insert into the orders table; the
  Assistant's role has no write grants at all.

This mirrors `trading-bot`'s existing pattern (its `web_trade` role is granted
`manual_orders` and nothing else, notably not `orders`) — the enforcement
point is the database grant, not application code trusting itself to behave.

## Alternatives considered

- A separate database or schema per agent, with explicit cross-agent read
  APIs. Rejected: the whole point is a *shared* knowledge base — partitioning
  it defeats the "two analysts converging is a signal" mechanism in ADR 0002,
  and multiplies operational surface (N databases to run and back up) for a
  system this size.
- Application-level permission checks only (any component *can* write
  anywhere, but the code chooses not to). Rejected: a permission a compromised
  or malfunctioning process can simply not check is not a permission boundary.

## Consequences

- Adding a new agent means provisioning one new database role with grants
  reviewed against the least-privilege checklist in
  `docs/policy/agent-management.md`, not touching any other role.
- A bug or bad output in any one agent is contained to what its role can
  write — the worst a broken Research agent can do is write bad reports, never
  a decision, a risk verdict, or an order.
