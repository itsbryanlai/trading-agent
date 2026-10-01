# UI dashboard

## Purpose

A read-only monitoring surface for the whole system, plus the one manual
control the design calls for. Not a trading interface — no trade can be
entered, modified, or approved from here.

## Views

- **Positions & equity** — current holdings, cash, an equity curve over time.
- **Order history** — every `orders` row, with the `risk_verdicts` and
  `decisions` row it traces back to (so a rejected or trimmed order is
  visible, not just filled ones).
- **Agent activity log** — every `reports` row from both analysts (including
  `no_action` runs) and every `decisions` row from the PM, rendering each
  row's Markdown rationale. Rationales are untrusted, model-written text that
  may quote the news: render them escaped (Markdown without raw HTML, links
  only to `http(s)` URLs), never as raw HTML (`specs/007-research-agent`).
- **Journal** — the daily narrative summary and the per-agent attribution
  view: how each analyst's ideas would have performed on their own, next to
  the actual blended portfolio result
  ([ADR 0002](../adr/0002-pm-synthesizes-rather-than-analysts-deciding.md)).
- **System state** — whether trading is currently paused, whether the
  daily-loss halt is active for the current session.

## Manual control

A single pause/resume toggle, writing `system_state.trading_paused`
directly — not routed through any agent
([ADR 0007](../adr/0007-assistant-is-separate-read-only-telegram.md)).

## Data access

Same shape as the Assistant's: broad read across every table, no write
grants except the one `trading_paused` toggle
([ADR 0004](../adr/0004-shared-postgres-role-scoped-credentials.md)).

## Non-goals

- No trade entry, modification, or approval of any kind.
- No editing of `config/risk.yaml` from the UI — risk config changes go
  through the review process in `docs/policy/agent-management.md`, not a
  form in the dashboard.

## Tech stack

FastAPI + Jinja2, server-rendered, deployed as its own Railway web service —
same stack and deployment shape as `trading-bot`'s existing status view
([ADR 0008](../adr/0008-dashboard-stack-and-research-provider.md)). No
frontend build step.
