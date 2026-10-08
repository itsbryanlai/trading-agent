# Versioning

How version numbers are written in this repo. Set by the owner on 2026-10-03.

## The scheme

| Change | Example | Meaning |
|---|---|---|
| Major | `v1`, `v2`, … | Not backwards compatible |
| Minor | `v1.1`, `v1.2`, … | Backwards compatible |
| Bug fix | `v1.1.1`, `v1.1.2`, … | Fixes only |
| Before the first release | `v0.1`, `v0.2`, … (`v0.1.1` if a fix needs its own number) | Still in development: anything may change |

Rules:

- **Start at `v0.1`, never `v1`.** Everything stays below `v1` until the first release.
- **The first release is `v1`.** It happens when the Research agent is stable and has its first release. At that point, everything versioned under this scheme moves to `v1` together.
- **Bump the smallest part that fits.** Before the first release, bump the minor part (`v0.1` → `v0.2`) for any change worth recording, and the patch part (`v0.1` → `v0.1.1`) only for a fix that needs its own number.
- **No trailing zeros.** Write `v1.1`, not `v1.1.0`, and `v1`, not `v1.0.0`. Add the third part only for a bug fix.
- **The `v` belongs in prose, logs and tags.** Version fields hold the bare number, for example `0.2`.

## What it applies to

| What | Where | Current |
|---|---|---|
| The project's release version | `pyproject.toml` `version` | `0.1` |
| Git tags for releases, when there are any | `vX`, `vX.Y`, `vX.Y.Z` | none yet |
| Research's prompt | `PROMPT_VERSION` in `src/trading_agent/research/prompt.py`, logged with every run | `0.2` |
| The Portfolio Manager's prompt | `PROMPT_VERSION` in `src/trading_agent/portfolio_manager/prompt.py`, logged with every run | `0.1` |
| The journal summary's template | `SUMMARY_VERSION` in `src/trading_agent/journal/summary.py`, logged with every run and stored in each row's attribution | `0.1` |
| The journal attribution's format | `schema_version` in `per_agent_attribution` (a bare integer; a run refuses a version it doesn't know) | `1` |
| Any future versioned prompt or interface of an agent | its own constant, logged the same way | — |

## What it doesn't apply to

These are sequence numbers or identifiers, not versions, and keep their own formats:

- **Migrations:** `NNNN_name.sql`, applied in order (`specs/001-data-model`).
- **ADRs:** `NNNN-title.md` (`docs/adr/README.md`).
- **Feature folders and branches:** `NNN-name`.
- **The risk config's `config_version`:** a hash of `config/risk.yaml`, written on every verdict (`specs/002-risk-gate`).
- **Dependency pins** in `pyproject.toml`: they follow each package's own versions.
- **The constitution** (`.specify/memory/constitution.md`): a governance document,
  not a release. It keeps its own `MAJOR.MINOR.PATCH` amendment numbering, set by
  its Governance section (currently `1.1.0`). Owner's decision, 2026-10-03.
