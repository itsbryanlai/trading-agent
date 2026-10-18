# Implementation Plan: The Dashboard

**Branch**: `014-dashboard` | **Date**: 2026-10-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/014-dashboard/spec.md`

## Summary

`python -m trading_agent.dashboard` serves the owner's dashboard on its own `dashboard` Railway service, the system's only public address (ADR 0026). It is one uvicorn process with one worker:
1. **Sign-in** (D4–D8): one password checked against a scrypt hash; a random session ID held in memory, in a `__Host-` cookie that is Secure, HttpOnly and SameSite=Strict, valid for 12 hours; site-wide and per-address "not before" delays; a CSRF value on every write.
2. **Read views** (D12, D19–D21): overview with the equity curve, order history, agent activity (reports, decisions, orchestrator runs), and the journal with a comparison chart and usage totals. All read as `ta_dashboard_login` in one read-only transaction per request, rendered on the server as HTML and inline SVG, with no script.
3. **Untrusted text** (D9, D10): restricted Markdown (no raw HTML, http(s) links only, no images), autoescape everywhere else, and a CSP that allows no script.
4. **The toggle** (D13–D15): confirmation first; pause always; resume refused unless the deployed `run_while_paused` is `false`; a market-hours warning; one conditional `UPDATE` through `ta_dashboard_control_login`, opened only for that write.
5. **Release** (D16, D22): migration `0015` adds the database-stamped `trading_paused_changed_at` (ADR 0027); `--check` and `--hash-password` for the owner; a health check; the Assistant names the dashboard as built.

**How it's built**: a pure core (`auth`, `throttle`, `toggle`, `render`, `timefmt`, `charts`, `attribution`, `pages`) tested with no network or database, and thin edges (`store`, `control`, `deployed`, `app`, `routes_*`, `check`, `__main__`). One migration, two logins, one config file, one service with four variables.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: existing psycopg 3.2, PyYAML, `exchange-calendars` (through `risk.calendar`) and `zoneinfo`; new `fastapi`, `jinja2`, `uvicorn`, `python-multipart`, `markdown-it-py`, and `httpx` for tests only (research D1, pinned there). Standard-library `hashlib.scrypt`, `hmac` and `secrets` for passwords, signatures and tokens.

**Storage**: the shared Postgres. Read as `ta_dashboard`; one two-column write as `ta_dashboard_control`. Migration `0015` adds one column and one trigger, and no grant (data-model.md).

**Testing**: pytest with Hypothesis.
- **Offline**: FastAPI's `TestClient` (in process) over a fake store and a fake control writer; property tests for the throttle, the link policy and the chart scaling; the hostile fixtures; template scans; the route-access table; the CSP on every route; log-marker fixtures; the control-URL guard; the deployed-shape guard.
- **Integration**: migration `0015`'s trigger by role; every query as the real login; the toggle as the real control login; denied writes; 13 logins; queries timed on a seeded year.
- **No network**: `tests/conftest.py`'s block stays.

**Target Platform**: Linux, a new always-on Railway web service, one replica, health check `/healthz`, Railway's default restart policy.

**Project Type**: a server-rendered web service in the existing `trading_agent` package.

**Performance Goals**: a typical page in under 2 s with a year of rows (SC-007). That is a few bounded queries with a 10 s statement timeout, and scrypt at about 0.1 s per sign-in.

**Constraints**:
- **No script on any page**, no outside resource, no frame.
- **Exactly one write**: `trading_paused` and `updated_at`, through `control.py` only.
- **Only `DASHBOARD_*` variables**; no model call, no broker or market-data key.
- **One process, one worker**: sessions and delays live in memory.
- **Logs** carry no secret, address, model text or database message.

**Scale/Scope**:

| Item | Size |
|---|---|
| Users | 1 (the owner) |
| Requests | a few a minute while in use |
| Sessions | at most 20 in memory |
| Rows per list page | 50 |
| Data at one year | ~3,500 snapshots; thousands of reports, decisions, verdicts and runs; ~250 journal rows |

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | No broker key; no route reaches the gate or Execution. The toggle sets the flag the gate and Execution already read. The new column is ignored by the trade path. | Pass |
| II. Analysts propose, PM decides | The dashboard decides nothing and writes nothing an agent reads except the pause flag, which is the owner's. | Pass |
| III. Least privilege at the database | No new grant. Two new logins for the two existing roles, as the grants contract designs (read path can't write; the toggle holds the one that can). `--check` and the integration tests prove each login's membership and its denials. Only `DASHBOARD_*` variables on its service, pinned by the guard test. | Pass |
| IV. Autonomous, one hard stop | The toggle is the "single plain toggle outside any agent's control". Refusing resume while `run_while_paused` is true enforces ADR 0021 §9's order; it is not a per-trade approval. The breaker is only displayed, never touched. | Pass |
| V. Spec and ADR first | ADR 0026 (the service, sign-in, logins, toggle, rendering) and ADR 0027 (the column and trigger), both accepted 2026-10-18, before this plan. `docs/specs/ui-dashboard.md` updated (clarify Q4). The data model, grants contract, service layout, logins contract, overview and runbook are updated, referencing them. | Pass |
| VI. Paper only, US equities | Not affected. | Pass |
| VII. Assistant and dashboard read-only | Read grants everywhere; one write, the toggle. No trade entry, approval or config edit (FR-026). | Pass |
| Technology section (v1.1.1) | FastAPI + Jinja2, server-rendered, no frontend build; a separate web service for the dashboard; Postgres the only durable store (sessions are memory only and losing them only signs the owner out). | Pass |

**Re-check after design**: all pass. The design adds one package, one config file, one migration (a column and a trigger), two logins, one Railway service with four variables and a health check, and five runtime dependencies. It adds no table, view or grant.

## Project Structure

### Documentation (this feature)

```text
specs/014-dashboard/
├── spec.md
├── plan.md                  # this file
├── research.md              # D1–D22
├── data-model.md
├── quickstart.md            # validation, and the owner's release steps R1–R7
├── contracts/
│   ├── dashboard-interface.md   # CLI, env, config, exit codes, logs, Railway service, outside changes
│   ├── routes.md                # every route, access rule, headers, cookie, errors
│   ├── queries.md               # every read query
│   ├── toggle.md                # decision table, pages, guarantees
│   └── rendering.md             # Markdown policy, hostile fixtures
├── checklists/requirements.md
└── tasks.md                 # /speckit-tasks
```

### Source code (repository root)

```text
src/trading_agent/
├── dashboard/
│   ├── __init__.py
│   ├── __main__.py        # env, modes (serve, --check, --hash-password), exit codes (D2)
│   ├── config.py          # config/dashboard.yaml loader and bounds (D17)
│   ├── auth.py            # pure: scrypt hash format and verify, cookie signing, Sessions, CSRF (D4–D6, D11)
│   ├── throttle.py        # pure: site-wide and per-address "not before" gates (D7)
│   ├── address.py         # the client's address from the request (D8)
│   ├── toggle.py          # pure: the decision table (contracts/toggle.md)
│   ├── render.py          # pure: restricted Markdown, the only Markup source (D9)
│   ├── timefmt.py         # pure: SGT with ET, ages (D19)
│   ├── charts.py          # pure: SVG polylines, ranges (D20)
│   ├── attribution.py     # pure: schema v1 parsing, comparison series, usage totals (D21)
│   ├── pages.py           # pure: view models from rows
│   ├── store.py           # every read query, one read-only transaction (D12, contracts/queries.md)
│   ├── control.py         # the one write; the only reader of DASHBOARD_CONTROL_DATABASE_URL (D13)
│   ├── deployed.py        # run_while_paused from the deployed schedule (D14)
│   ├── app.py             # app factory, headers middleware, auth dependency, error pages (D10)
│   ├── routes_auth.py     # sign-in, sign-out
│   ├── routes_pages.py    # overview, orders, activity, journal, healthz
│   ├── routes_toggle.py   # /toggle, /toggle/done
│   ├── check.py           # --check (D16)
│   ├── templates/         # base, sign_in, overview, orders, activity, journal, toggle, toggle_done, error
│   └── static/style.css
├── storage/
│   ├── logins.py          # + ta_dashboard_login, ta_dashboard_control_login
│   └── migrations/0015_trading_paused_changed_at.sql
└── assistant/replies.py   # CONTROLS[0] wording

config/dashboard.yaml                  # new
pyproject.toml                         # dependencies; dashboard in the top layer; package data
.env.example, .railway/railway.ts, .railway/README.md

tests/
├── fakes/dashboard_store.py, fakes/dashboard_control.py
├── unit/dashboard/                    # one file per module; routes access table; hostile fixtures;
│                                      # template scans; CSP; log markers; control-URL guard
├── unit/deploy/test_deployed_shape.py # the dashboard service, its variables, health check, networking
├── unit/storage/test_logins.py        # 13 rows
├── unit/assistant/                    # the new CONTROLS text passes the link filter
└── integration/
    ├── dashboard/                     # every query as the login; the toggle as the control login; timings
    └── storage/                       # 0015 trigger by role; test_logins 13 rows; grants unchanged

docs/specs/data-model.md, specs/001-data-model/contracts/role-grants.md, docs/architecture/overview.md,
docs/specs/assistant-agent.md, docs/operations/deployment.md (§3, §5, §9, new §10),
specs/010-observe-only-deployment/contracts/{service-layout,logins-command}.md,
specs/013-assistant/contracts/model-io.md
```

**Structure Decision**: a new top-layer package. It imports `storage.db` and `risk.calendar`, and no sibling (D3). Routes are split by area from the start, and the SQL stays in `store.py`, so no module nears 600 lines. The only other component code touched is two rows in `storage/logins.py`, one string in `assistant/replies.py` and one migration.

## Things flagged for the owner

1. **Sign-in under sustained attack** (D7). With a site-wide limit, a scripted attacker who fires the moment each wait ends wins most races for the next slot, so while an attack lasts your sign-in can be held off. It isn't a fixed lockout: it ends when the attack stops. Guessing stays capped at about 2,880 a day against a 16+ character password. Existing sessions keep working, the runbook's manual `UPDATE` still works, and failures show in the logs. The spec's edge case ("waits at most 30 s") holds only without such an attacker. **Accepted by the owner on 2026-10-18; the spec is corrected (FR-004, edge cases).**
2. **The public address** is generated by you in Railway after apply, since its name isn't known in advance. R5 checks that a later `railway config plan` doesn't propose removing it; if it does, the name goes into `.railway/railway.ts`.
3. **Five new runtime dependencies** (D1), pinned to versions at least two weeks old: `fastapi 0.142.x`, `jinja2 3.1`, `uvicorn 0.54`, `python-multipart 0.0.32+`, `markdown-it-py 4.2+`; and `httpx` for tests.
4. **A release with a migration**: R1 runs `0015` before the code is merged into `release/prod`. The column is null until the next pause change, so the banner reads "start time not recorded" until then.
5. **Defaults for you to adjust** (`config/dashboard.yaml`): 12-hour sessions, 50 rows a page, 20 sessions, delays 1 s doubling to 30 s, 1,000 addresses tracked, 10 s statement timeout.
6. **New owner steps**: the two logins, the password hash, the session secret, `--check`, the domain, and the first visit (quickstart R1–R7).
7. **Not touched**: the trade path, every grant, `risk.yaml`, `schedule.yaml`, `run_while_paused` and the pause itself.
