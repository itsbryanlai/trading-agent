# CLAUDE.md

Rules for AI assistants (and contributors) working in this repo.

## Current phase: implementation

Design is settled — see [`docs/adr/0009-implementation-phase-started.md`](docs/adr/0009-implementation-phase-started.md). Application code, `config/risk.yaml`, and `.env.example` are all in scope now, alongside the existing docs.

- Existing specs in `docs/specs/` are the source of truth for *behavior* — read the relevant one before implementing a component, and don't silently reinterpret its inputs/outputs/edge cases/non-goals.
- A design discussion that changes direction still gets an ADR (see [`docs/adr/README.md`](docs/adr/README.md) for the template) before the code changes, not a silent edit to `docs/architecture/overview.md` or a spec. Update the overview/spec after the ADR lands, referencing it.
- Adding a new agent or service follows [`docs/policy/agent-management.md`](docs/policy/agent-management.md) — spec and (if it changes the system's shape) an ADR before any implementation code.

## Implementation rules

- Never commit secrets, API keys, or exchange/broker credentials. Use environment variables and keep an up-to-date `.env.example`.
- Never place an order, execute a trade, or move funds — including against a paper/sandbox account — without the user explicitly asking for that specific action in the current conversation.
- Flag any change to risk limits, position sizing, or order logic explicitly before applying it.
- Run relevant tests after modifying any logic file.
- Prefer small, focused changes over large rewrites.
- Version numbers follow [`docs/policy/versioning.md`](docs/policy/versioning.md): `v0.1`, `v0.2`, … until the first release, which is `v1`; then `v1.1` for compatible changes, `v1.1.1` for fixes, `v2` for breaking ones.

## Off-limits, always

- Don't touch `docs/adr/` entries after they're accepted — supersede with a new ADR instead of editing history.
- Don't invent strategy names, broker integrations, or dates that weren't discussed — flag the gap and ask instead of filling it in.
