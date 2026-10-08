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
- **Test in two tiers.** After modifying any logic file, run the tests covering the touched modules, plus `scripts/lint.sh`. Run the full unit suite and the integration suite once, when a feature or fix is finished, before pushing; CI runs both again on the PR. The full suites take minutes, so running them after every task or phase is what makes a build slow.
- Prefer small, focused changes over large rewrites.
- **Atomic commits, always.** One logical change per commit, committed as you finish it — never batched at the end of a feature. Don't mix refactors with behavior changes, or code with unrelated docs. Use the message style already in `git log` (`Area (feature NNN): what changed (task/review ID)`).
- **Modular code, always.** Keep each module to a single responsibility. Components follow the layering `execution | orchestrator | research | portfolio_manager` → `reference` → `risk` → `llm | storage`: depend only on layers below, and never import a sibling. Split a module before it grows past the size limit; don't extend one that is already over it.
- Both rules are enforced mechanically; fix the cause rather than working around it:
  - Commits: `.claude/hooks/check-atomic-commit.py` (Claude Code `PreToolUse` hook) blocks a `git commit` over 15 code files or 600 changed lines (docs, specs and `.md` files don't count). If the commit really is one change, split it by staging a subset; add `[large-commit]` to the message only as a last resort.
  - Modularity: `scripts/lint.sh` runs `ruff` (complexity limits), `lint-imports` (the layering above, configured in `pyproject.toml`) and a module-size check (`scripts/check_module_size.py`, 600 lines). Run it after modifying any logic file, alongside the tests. Don't add `# noqa` or raise a limit to get past it; if a limit looks wrong, flag it instead.
- Version numbers follow [`docs/policy/versioning.md`](docs/policy/versioning.md): `v0.1`, `v0.2`, … until the first release, which is `v1`; then `v1.1` for compatible changes, `v1.1.1` for fixes, `v2` for breaking ones.

## Off-limits, always

- Don't touch `docs/adr/` entries after they're accepted — supersede with a new ADR instead of editing history.
- Don't invent strategy names, broker integrations, or dates that weren't discussed — flag the gap and ask instead of filling it in.
