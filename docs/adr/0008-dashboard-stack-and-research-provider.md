# 0008. Dashboard stack and Research's news provider

Status: accepted

## Context

Two of `docs/architecture/overview.md`'s open items needed a call: what the
dashboard is built with, and where Research's news comes from. Neither
changes the system's shape, but both were blocking implementation from
starting cleanly.

## Decision

**Dashboard**: FastAPI + Jinja2, server-rendered, deployed as its own Railway
web service — the same stack and deployment shape `trading-bot` already uses
for its status view. No frontend build step, no second web framework in the
project's toolset.

**Research's primary news provider**: Finnhub's free tier — per-symbol
company news with structured fields (headline, url, source, datetime) that
map directly onto the `sources` field in `docs/specs/data-model.md`, at a
rate limit that supports daily plus triggered runs. Its own credential,
separate from Alpaca and Anthropic, per
[ADR 0004](0004-shared-postgres-role-scoped-credentials.md).

## Alternatives considered

- **Alpaca's own news endpoint** for Research. Rejected: it uses the same
  account credential as market data/trading, conflicting directly with the
  Research spec's requirement that its news credential stay separately
  scoped — a compromised or rate-limited news call should never be able to
  touch anything with trading access.
- **Alpha Vantage's `NEWS_SENTIMENT`** as the primary provider. Not rejected
  outright — it usefully pre-computes a per-article sentiment score, which
  could feed `conviction` — but its free-tier rate limit (historically ~25
  requests/day) is too tight to be the primary feed. Left as a candidate
  secondary signal to add later, not a blocker now.
- A JS-framework dashboard (React/Vue SPA). Rejected: adds a build pipeline
  and a second deployment shape for no benefit at this scope — read-only
  views and one toggle button don't need client-side interactivity.

## Consequences

- The dashboard can be deployed with the exact `railway.web.json` pattern
  `trading-bot` already validated in production.
- Research's report quality is bounded by Finnhub's coverage; if that proves
  too thin, Alpha Vantage's sentiment score is the documented next step
  rather than an unplanned pivot.
