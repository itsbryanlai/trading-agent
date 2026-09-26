# CLAUDE.md

Rules for AI assistants (and contributors) working in this repo.

## Current phase: docs-only

This repo is in design phase. Until an ADR in [`docs/adr/`](docs/adr/README.md) declares implementation started:

- Write `.md` files only. No application code, no config beyond what's already here.
- A design discussion that changes direction gets an ADR (see [`docs/adr/README.md`](docs/adr/README.md) for the template), not a silent edit to `docs/architecture/overview.md`. Update the overview after the ADR lands, referencing it.
- Specs in `docs/specs/` describe behavior (inputs, outputs, edge cases), not implementation. If you catch yourself writing function signatures or pseudocode, stop — that's an implementation-phase task.

## Once implementation starts

- Never commit secrets, API keys, or exchange/broker credentials. Use environment variables and keep an up-to-date `.env.example`.
- Never place an order, execute a trade, or move funds — including against a paper/sandbox account — without the user explicitly asking for that specific action in the current conversation.
- Flag any change to risk limits, position sizing, or order logic explicitly before applying it.
- Run relevant tests after modifying any logic file.
- Prefer small, focused changes over large rewrites.

## Off-limits, always

- Don't touch `docs/adr/` entries after they're accepted — supersede with a new ADR instead of editing history.
- Don't invent strategy names, broker integrations, or dates that weren't discussed — flag the gap and ask instead of filling it in.
