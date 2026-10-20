# 0026. The dashboard is a public web service with the owner's sign-in, two database logins, and a guarded toggle

Status: accepted

Accepted by the owner on 2026-10-18, before `/speckit-specify` for feature 014. The owner chose every decision below.

## Context

[0008](0008-dashboard-stack-and-research-provider.md) fixed the dashboard's stack: FastAPI + Jinja2, server-rendered, its own Railway web service, no frontend build. [`docs/specs/ui-dashboard.md`](../specs/ui-dashboard.md) defines its views and its one control, a plain pause/resume toggle writing `system_state.trading_paused` ([0007](0007-assistant-is-separate-read-only-telegram.md), Constitution IV and VII). The roles exist: `ta_dashboard` reads every table and view, and `ta_dashboard_control` may only read `system_state` and update `trading_paused` and `updated_at` (`specs/001-data-model/contracts/role-grants.md`). Five things are undecided, and each changes the system's shape:

- **Exposure.** Every service today is outbound-only; the Assistant long-polls Telegram so that it accepts no inbound connection ([0023](0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §2). A web page the owner opens from a phone needs a public endpoint, and so a sign-in, sessions, and protection against cross-site requests on the toggle.
- **Credentials.** The grants contract splits the dashboard into a request path that cannot write and a toggle that can. The only logins for those roles today are the owner's own psql logins, `ta_owner_read_login` and `ta_owner_control_login`.
- **The toggle's order.** Leaving observe-only is two steps ([0021](0021-railway-deployment-as-code-observe-only-first.md) §9): first a released change sets `portfolio_manager.run_while_paused: false` while still paused, then the owner clears the pause after a close. A button that clears the pause first would leave the window 0021 warns about, where re-pausing would not stop the Portfolio Manager.
- **Untrusted text on a web page.** Rationales, reasoning, journal summaries and broker reasons are written by a model or a broker. The Assistant sends plain text with a link filter; HTML needs real escaping and a link policy.
- **Data and deployment.** Where the equity curve comes from, how fresh pages are, how the service is checked before release and how its health is reported.

## Decision

1. **Its own Railway service, `dashboard`,** always on, one replica, started as `python -m trading_agent.dashboard` and listening on Railway's `$PORT`, with Railway's generated public domain. It holds only `DASHBOARD_*` variables, and Railway's default on-failure restart applies. It runs whether or not trading is paused. The orchestrator does not schedule or start it.
2. **One owner, signed in by password.** The password is stored only as a hash, in `DASHBOARD_PASSWORD_HASH`, which the owner makes on their own machine. Signing in sets a session cookie signed with `DASHBOARD_SESSION_SECRET`: HttpOnly, Secure, SameSite=Strict, expiring after about 12 hours. Repeated failed sign-ins are slowed down. Every page except the sign-in page and the health check requires a session. Every form that writes carries a CSRF token checked by the server.
3. **Two logins in one process, matching the grants contract.** `DASHBOARD_DATABASE_URL` connects as a new `ta_dashboard_login` (member of `ta_dashboard`) and serves every page. `DASHBOARD_CONTROL_DATABASE_URL` connects as a new `ta_dashboard_control_login` (member of `ta_dashboard_control`). It is opened only inside the toggle's handler, for one transaction, and closed. A guard test checks that no other module reads it. The owner's `ta_owner_*` logins stay the owner's. This is the split the contract already describes, not an exception to Constitution III.
4. **The toggle is guarded, and stays a plain button.**
   - Pressing it opens a confirmation page stating the current state, since when, and what the change does. Only confirming writes.
   - **Pausing is always allowed.**
   - **Resuming is refused while the deployed `config/schedule.yaml` has `portfolio_manager.run_while_paused: true`.** The page says step 1 of the switch-on is not released, and points to the deployment runbook. This enforces 0021 §9's order; it is not a per-trade approval ([0006](0006-autonomous-operation-with-daily-loss-breaker.md)).
   - **Resuming while the market is open shows a warning** on the confirmation page, and is allowed.
   - The write sets `trading_paused` and `updated_at` and nothing else. Each toggle writes one log line with its direction and time. There is no audit table.
   - No agent, model or natural-language interface reaches it; the Assistant still cannot.
5. **Reads.** The views are the spec's. The equity curve comes from `account_snapshots`: every snapshot for ranges up to a week, and the last snapshot of each trading day beyond that. It is drawn as inline SVG on the server. Pages are refreshed by reloading; each shows when it was read and how old its data is. Lists are newest first, 50 rows a page, filterable by date, symbol and agent, with long text collapsed. Times are shown in Asia/Singapore with ET alongside, as the Assistant does. The pages need no JavaScript. Besides the database, the service reads exactly one deployed setting, `portfolio_manager.run_while_paused`, for the toggle and the banner.
6. **Model- and broker-written text is untrusted.** Markdown is rendered with `markdown-it-py` with raw HTML disabled. A link is kept only if its URL is `http` or `https`; any other is shown as plain text. Templates autoescape. Every response carries a Content-Security-Policy allowing no scripts and no resources from other origins, and forbidding framing.
7. **Credentials:** `DASHBOARD_DATABASE_URL`, `DASHBOARD_CONTROL_DATABASE_URL`, `DASHBOARD_PASSWORD_HASH` and `DASHBOARD_SESSION_SECRET`. No broker, model or market-data key. The dashboard makes no model call ([0018](0018-qwen-as-a-model-provider.md)).
8. **Health and release checks.** An unauthenticated `/healthz` runs `SELECT 1` on the read login and returns only `ok`, or 503, with no data; Railway's health check uses it. A `--check` command, run by the owner before a release that changes the dashboard, prints one line per item, each of which must read `ok`: the read login's and the control login's role membership (checked without writing), the password hash's format, the session secret's length, and that `run_while_paused` is readable.
9. **Observe-only is shown on every page** while paused: a banner saying trading is paused, since when, whether the Portfolio Manager still runs, and that the gate rejects every buy.
10. **The Assistant names the dashboard as built.** Its first control becomes "the dashboard's pause control", with "(not built yet)" dropped, in `assistant/replies.py`, `specs/013-assistant/contracts/model-io.md` and the runbook. The runbook's sections 5 and 6 stay its second control. Neither contains a file name or domain, so the link-filter test still passes.

## Alternatives considered

- **A one-time code from an authenticator app as a second factor.** Not chosen for now: one more secret and setup step for a paper account. It can be added later by its own decision.
- **An identity proxy** (Cloudflare Access, Tailscale). Not chosen: a new external dependency and account for one user.
- **An IP allowlist.** Rejected: Railway has none built in, and the owner's address changes on mobile.
- **Two services, a read-only site and a separate control service.** Rejected: the toggle would need an internal endpoint with its own authentication, more surface for the same grant.
- **Reusing the owner's `ta_owner_*` logins.** Not chosen: resetting the service's password would break the owner's psql access, and connections could not be told apart.
- **Only warning when resuming with `run_while_paused: true`.** Not chosen: the order matters for safety, and code can check it.
- **Refusing to resume while the market is open.** Not chosen: once `run_while_paused` is false, nothing is approved while paused, so a resume during the session carries no observed approval into an order.
- **A table recording each toggle.** Not chosen: a migration and a new grant to record what `updated_at` and a log line already show, for one user.
- **Reloading pages automatically, or a JavaScript chart library from a CDN.** Not chosen: scripts and outside resources break the no-script policy, for data that changes every few minutes.
- **Plain escaped text with no Markdown.** Safer still, but rationales are written as Markdown and would read poorly.

## Consequences

- **The system's first public endpoint.** The sign-in page is reachable from the internet, and the password is the one factor. Whoever holds a session can read every table and pause or resume trading. They cannot trade, approve, size or change risk settings: the control login can update two columns of `system_state` and nothing else (ADR 0004). The account is paper (Constitution VI).
- **A seventh service.** `.railway/railway.ts`, `tests/unit/deploy/test_deployed_shape.py` and `specs/010-observe-only-deployment/contracts/service-layout.md` gain a `dashboard` entry holding only `DASHBOARD_*` variables, and the only service with a public domain. `storage/logins.py` gains `ta_dashboard_login` and `ta_dashboard_control_login`. No migration and no grant change.
- **New Python dependencies:** FastAPI, Jinja2 (both named by the constitution), an ASGI server, a form parser and `markdown-it-py`.
- **New owner steps:** making the password hash and session secret on their own machine, creating the two logins, running `--check`, and the first sign-in. The runbook gains a section for them.
- **The toggle reads a deployed file.** Its refusal reflects what was released to `release/prod`, not `main`, as the Assistant's settings do ([0024](0024-assistant-explains-refusals-and-reads-deployed-settings.md) §4).
- **Runbook section 5's manual `UPDATE` as `ta_owner_control_login` stays valid,** and bypasses the toggle's guard; the runbook should say the dashboard is the preferred path.
- **No constitution amendment.** The deployment bullet already names a separate web service for the dashboard, and Principle VII already allows its one toggle.
