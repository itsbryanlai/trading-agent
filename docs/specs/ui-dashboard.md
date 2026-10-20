# UI dashboard

Built as feature 014 ([`specs/014-dashboard`](../../specs/014-dashboard/spec.md);
[ADR 0026](../adr/0026-dashboard-public-service-with-owner-sign-in.md),
[ADR 0027](../adr/0027-pause-change-time-recorded-by-the-database.md),
[ADR 0028](../adr/0028-dashboard-resume-needs-the-orchestrator-to-have-skipped-a-pm-run.md)).
The spec is the source of truth for behavior; this page is the overview.

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
  Every `orchestrator_runs` row too, with failed, timed-out and skipped runs
  marked, so a missing report can be told apart from a run that found
  nothing (`specs/014-dashboard`).
- **Journal** — the daily narrative summary and the per-agent attribution
  view: how each analyst's ideas would have performed on their own, next to
  the actual blended portfolio result
  ([ADR 0002](../adr/0002-pm-synthesizes-rather-than-analysts-deciding.md)).
- **System state** — whether trading is currently paused, whether the
  daily-loss halt is active for the current session.

## Sign-in

The dashboard is the system's only public address, so every page except the
sign-in page and the health check needs a session. One owner password (stored
only as a hash) signs in; the session is an HttpOnly, Secure, SameSite=Strict
cookie that expires after about 12 hours and is revocable; failed sign-ins are
slowed down; every write carries a CSRF value
([ADR 0026](../adr/0026-dashboard-public-service-with-owner-sign-in.md) §2).
Pages run no script and load nothing from outside, and model-written text is
rendered as restricted Markdown ([ADR 0026](../adr/0026-dashboard-public-service-with-owner-sign-in.md) §6).

## Manual control

A single pause/resume toggle, writing `system_state.trading_paused`
directly — not routed through any agent
([ADR 0007](../adr/0007-assistant-is-separate-read-only-telegram.md)). It is a
plain button with a confirmation page, and it is guarded
([ADR 0026](../adr/0026-dashboard-public-service-with-owner-sign-in.md) §4,
[ADR 0028](../adr/0028-dashboard-resume-needs-the-orchestrator-to-have-skipped-a-pm-run.md)):

- **Pausing is always allowed.**
- **Resuming is refused** unless the deployed `portfolio_manager.run_while_paused`
  is `false` *and* the orchestrator's newest Portfolio Manager run was skipped
  because of the pause; an unreadable setting or missing evidence also refuses.
  This keeps [ADR 0021](../adr/0021-railway-deployment-as-code-observe-only-first.md)
  §9's order. It is not a per-trade approval.
- Resuming while the market is open shows a warning and is allowed, as an
  emergency fallback only; the runbook's instruction stays "after the close and
  before the next open".
- The write sets `trading_paused` and `updated_at` only; the database stamps
  when the pause changed ([ADR 0027](../adr/0027-pause-change-time-recorded-by-the-database.md)).
  Every page shows an observe-only banner while paused.

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
