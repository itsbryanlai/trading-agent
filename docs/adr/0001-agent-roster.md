# 0001. Agent roster: three analysts-and-decider agents, plus a read-only Assistant

Status: accepted

## Context

The system needs multiple specialized roles — research, opportunistic scanning,
deciding, executing, and answering questions about the whole thing — without
collapsing into one do-everything agent or, at the other extreme, a pile of
agents with overlapping, unclear responsibilities.

## Decision

Four LLM-driven agents, two deterministic services:

- **Research** — sources news, produces sentiment-driven trade proposals
- **Opportunistic Identifier** — scans the market for undervalued names
- **Portfolio Manager (PM)** — the sole decision-maker; reads both agents'
  proposals and decides direction and size independently (see
  [0002](0002-pm-synthesizes-rather-than-analysts-deciding.md))
- **Assistant** — read-only, answers questions about portfolio/agent state over
  Telegram (see [0007](0007-assistant-is-separate-read-only-telegram.md))
- **Risk Gate** (deterministic) — vets every PM decision against a config file
- **Execution** (deterministic) — the only component holding broker credentials

## Alternatives considered

- A single "decision layer" agent (as in `trading-bot`) doing research and
  deciding in one model call. Rejected: conflates sourcing an idea with
  judging it, and can't be evaluated per-role (see 0002 for the bias argument).
- Execution as its own LLM agent with judgment over order mechanics. Rejected
  in [0005](0005-risk-gate-and-execution-are-deterministic.md) — no order-working
  problem exists at this trading pace that a fixed rule can't solve, and giving
  an LLM output a path to the broker is a risk this design specifically avoids.

## Consequences

- Every trade is traceable to exactly one deciding agent (PM) and the report(s)
  that fed it.
- Adding a new analyst agent later (see `docs/policy/agent-management.md`) only
  means teaching the PM to consider one more report source — it does not touch
  the Risk Gate or Execution.
- The roster has a hard line between "has judgment" (LLM agents) and "applies
  fixed rules" (Risk Gate, Execution) that every future addition must fall on
  one side of.
