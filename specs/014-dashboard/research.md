# Research: The Dashboard

Decisions for `specs/014-dashboard`, numbered D1–D22. Binding inputs: [ADR 0026](../../docs/adr/0026-dashboard-public-service-with-owner-sign-in.md), [ADR 0027](../../docs/adr/0027-pause-change-time-recorded-by-the-database.md), the spec's Clarifications, and Constitution I–VII.

## D1. Web stack and versions

**Decision**: FastAPI and Jinja2, served by uvicorn, as named by the constitution and ADR 0008. New runtime dependencies, each pinned to a minor version at least two weeks old on 2026-10-18:

| Package | Pin | Why |
|---|---|---|
| `fastapi` | `>=0.142.2,<0.143` | routes, forms, dependency injection; 0.142.2 is from 2026-09-30 (0.143.x is under two weeks old) |
| `jinja2` | `>=3.1.6,<3.2` | templates, with autoescape |
| `uvicorn` | `>=0.54,<0.55` | the ASGI server, one process, one worker |
| `python-multipart` | `>=0.0.32,<0.1` | FastAPI's form parsing (sign-in, toggle) |
| `markdown-it-py` | `>=4.2,<5` | Markdown rendering (D9) |

Dev only: `httpx>=0.28,<0.29`, for FastAPI's `TestClient`. It runs the app in process, so `tests/conftest.py`'s network block is untouched.

**Rationale**: the stack is already decided; the pins follow the repo's existing style (`psycopg[binary]>=3.2,<3.3`).

**Alternatives**: Starlette alone (FastAPI is the named stack); gunicorn with workers (in-memory sessions and delays need exactly one process, D6, D7).

## D2. Process shape

**Decision**: `python -m trading_agent.dashboard` starts uvicorn programmatically: `host 0.0.0.0`, `port $PORT`, one worker, uvicorn's access log off (D18), no proxy-header trust (D8). Handlers are synchronous (`def`) and run in FastAPI's thread pool, because psycopg is used synchronously everywhere in the repo. Modes: no flag serves; `--check` (D16); `--hash-password` (D5). SIGTERM is uvicorn's graceful shutdown.

**Rationale**: matches the other services' `python -m` entry points (ADR 0021 §3). One process is what makes in-memory sessions and delays correct.

## D3. Layering and modules

**Decision**: a new top-layer package `trading_agent.dashboard`, added to the import-linter top layer in `pyproject.toml`. It imports `storage.db` and `risk.calendar` (both lower layers, allowed) and no sibling: in particular not `assistant` or `orchestrator`, so the time formatting, the settings read and the schedule read are its own small modules. Templates and the stylesheet are package data (`[tool.setuptools.package-data]`, as `storage/migrations/*.sql` already is), so they ship with the editable install.

Pure core, testable with no database or network: `auth`, `throttle`, `toggle`, `render`, `timefmt`, `charts`, `attribution`, `pages` (view models). Edges: `store`, `control`, `deployed`, `app`, `routes_*`, `check`, `__main__`. Every module stays under 600 lines; `store.py` (the SQL) and `app.py` are the likeliest to grow, and the routes are split by area from the start.

## D4. Sign-in and session cookie

**Decision**:
- One password, checked against `DASHBOARD_PASSWORD_HASH` (D5). The sign-in form has a single password field.
- On success, a session ID from `secrets.token_urlsafe(32)` is stored in memory (D6). The cookie value is `<id>.<HMAC-SHA256(DASHBOARD_SESSION_SECRET, id), base64url>`, so ADR 0026 §2's "signed with the session secret" holds and a malformed or forged cookie is rejected before the session table is looked up.
- Cookie: name `__Host-ta_dashboard`, `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`, no `Domain`, `Max-Age` 12 hours. The `__Host-` prefix makes browsers refuse it unless it is Secure, host-only and path `/`.
- Expiry is absolute, 12 hours from sign-in (config, D17), not sliding.
- Every page except `GET/POST /sign-in`, `GET /healthz` and `GET /static/*` requires a valid session; otherwise `303` to `/sign-in` (pages) or `403` (writes). A route-table test enumerates every route and asserts this (SC-006).
- Sign-out is a `POST /sign-out` with the CSRF value; it deletes the session and clears the cookie.

**Rationale**: the standard shape for a single-user web app; `__Host-`, HttpOnly and SameSite=Strict close cookie theft by script, subdomain and cross-site sending.

**Alternatives**: Starlette's `SessionMiddleware` (a signed cookie holding the whole session, which can't be revoked; clarify Q2 chose revocable).

## D5. Password hash

**Decision**: standard-library `hashlib.scrypt` (`n=2**15, r=8, p=1, dklen=32`, 16-byte random salt), stored as `scrypt$32768$8$1$<salt b64>$<hash b64>`. Verification recomputes and compares with `hmac.compare_digest`. `python -m trading_agent.dashboard --hash-password` reads the password twice with `getpass` (never echoed), refuses fewer than 16 characters or a mismatch, and prints only the hash line to stdout (FR-004a). It needs no other variable and no database.

**Rationale**: no new dependency; scrypt is memory-hard and in the standard library since 3.6. About 50–100 ms per check, bounded by the throttle (D7).

**Alternatives**: `argon2-cffi` or `bcrypt` (a native dependency for one hash).

## D6. Sessions in memory

**Decision**: a `Sessions` object holding `{id: (created_at, expires_at, csrf)}` behind a lock, with an injected clock. At most 20 live sessions; creating a 21st drops the oldest. Expired entries are removed on lookup and on each new sign-in. A restart empties it (FR-003). Nothing is written to the database or disk.

**Rationale**: clarify Q2. One user; a bound keeps memory fixed even if sessions are created repeatedly.

## D7. Sign-in delays (owner to confirm the residual, see "Things flagged")

**Decision**: two "not before" gates in memory, with an injected clock:
- **Site-wide**: after a failure, the next attempt is not accepted until `1 s × 2^(failures−1)`, capped at 30 s, has passed. A success resets it.
- **Per address** (D8): the same curve for the client's address, added to the site-wide wait. A success resets that address. At most 1,000 addresses are tracked, oldest first out.
- An attempt that arrives too early is answered at once with "Too many attempts. Try again in N seconds.", without checking the password and without counting as a failure. Nothing sleeps, so a flood can't exhaust the thread pool.
- There is no lockout: once the wait passes, an attempt is always checked (FR-004).

**Rationale**: sleeping before answering would let parallel requests each wait their delay and then be checked, so the site-wide cap would not cap anything. "Not before" bounds guessing to at most one checked attempt per 30 s site-wide, about 2,880 a day, against a password of 16 or more characters.

**Residual, which the spec's edge case understates**: a scripted attacker who fires an attempt the moment each wait ends wins the race for the next slot, so while the attack lasts, the owner's own attempts mostly get "try again in N seconds". The owner isn't locked out in the sense of a fixed lockout, but sign-in can be held off until the attack stops. Existing sessions keep working (12 hours), and the runbook's manual `UPDATE` remains a path for the pause. Each failure writes one log line (D18), so an attack shows in the service's logs.

**Alternatives**: sleep-then-check (defeated by parallel requests); a token bucket of several attempts a minute (the same race, with more guesses); per-address only (no cap on distributed guessing).

## D8. The client's address

**Decision**: the per-address key is the last entry of `X-Forwarded-For`, the one Railway's edge proxy appends; with no such header (local runs, tests), the socket peer. uvicorn's own proxy-header handling stays off, because with `forwarded_allow_ips="*"` it would trust a client-supplied leftmost entry. Quickstart step R6 verifies on the first deploy that a forged `X-Forwarded-For` does not change the key.

**Rationale**: only the per-address delay depends on it, and it is the secondary layer: the site-wide delay holds even if the address can be spoofed.

## D9. Rendering untrusted text

**Decision**: `markdown-it-py`, `MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})`, then:
- the `image` renderer replaced: `![alt](url)` renders as its escaped alt text, never an `<img>` (disabling the parse rule instead would turn it into "!" plus a link);
- `validateLink` replaced: a link is kept only if its URL, stripped and lower-cased, starts with `http://` or `https://`; otherwise the whole link renders as its text;
- every kept link renders with `rel="nofollow noopener noreferrer"` and `target="_blank"`;
- headings are capped at `<h4>` so a rationale can't break the page's outline.

Fields rendered this way: `reports.rationale_md`, `decisions.reasoning_md`, `journal.summary_md`. Source titles, broker reasons, refusal details, run details and every other stored value are plain text through Jinja autoescape (FR-016). The rendered HTML is marked safe only at the one call site in `render.py`; nothing else uses `|safe`, and a test greps the templates for it.

**Rationale**: `html=False` escapes raw HTML; the link and image rules close `javascript:`, `data:` and outside requests. The CSP (D10) is the second layer. Hostile fixtures cover SC-003 (contracts/rendering.md).

**Alternatives**: plain text with linkify (ADR 0026 rejected as less readable); `bleach` sanitising HTML (deprecated upstream, and a sanitiser after rendering is the harder direction).

## D10. Response headers

**Decision**: one middleware sets, on every response:
- `Content-Security-Policy: default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer` (an outside link never learns the dashboard's address)
- `Strict-Transport-Security: max-age=31536000`
- `Cache-Control: no-store` on everything except `/static/*`.

There is no `script-src`, so no script runs, inline or not. Charts are inline SVG using presentation attributes and classes from the one stylesheet; no `style=` attributes are emitted (a test checks the templates).

## D11. Cross-site check on writes

**Decision**: each session holds a random CSRF value (`secrets.token_urlsafe(32)`), placed as a hidden field in every write form. Every `POST` except `/sign-in` must carry it, compared with `hmac.compare_digest`, and must have an `Origin` header that is absent or equal to the request's own origin. A failure is `403` with "Request refused. Nothing was changed." The sign-in `POST` checks `Origin` only.

**Rationale**: SameSite=Strict already stops cross-site sending; the token is ADR 0026 §2's required check and holds if a browser mishandles SameSite.

## D12. Database access, read path

**Decision**: one connection per request through `storage.db.connect(DASHBOARD_DATABASE_URL)`, then `SET TRANSACTION READ ONLY` and `SET LOCAL statement_timeout` (config, 10 s), all of a page's queries in that one transaction. Queries are constant SQL with bound parameters, in `store.py` only (contracts/queries.md). Every list is `ORDER BY <time> DESC, id DESC LIMIT 51 OFFSET :offset`, 50 shown and the 51st telling whether a next page exists. A database failure renders a "database unavailable" page with status 503 and no error text; its log line names the exception type only.

**Rationale**: one user and a few requests a minute; a pool adds a dependency and stale-connection handling for no gain. `READ ONLY` is defence in depth: `ta_dashboard` already can't write.

## D13. The toggle's write

**Decision**: `control.py` is the only module that reads `DASHBOARD_CONTROL_DATABASE_URL`. A confirmed toggle opens one connection, runs

```sql
UPDATE system_state SET trading_paused = %(target)s, updated_at = now()
WHERE trading_paused = %(expected)s
```

and commits; one updated row is success, zero rows is "the state changed; nothing was done" (FR-023). Then it closes. The confirmation form carries `expected` (the state the confirmation page showed) and `target`; the server checks `target = not expected`. Before writing a resume, it re-reads the deployed setting (D14) and refuses if it is not `false`. A guard test checks that the variable name appears in `control.py` and `check.py`'s call into it, and nowhere else in `src/` (FR-018).

**Rationale**: `ta_dashboard_control` has `S` on `system_state`, so the `WHERE` is allowed; the conditional update makes a stale confirmation harmless without a lock.

## D14. The deployed setting

**Decision**: `deployed.py` reads `config/schedule.yaml` from the repository root (as the other config loaders resolve it, `Path(__file__).parents[3]`) with `yaml.safe_load`, and returns `portfolio_manager.run_while_paused` only if it is a real boolean; otherwise "unreadable". It is read on each page load (the file is tiny) so the banner and the toggle never disagree with what is deployed. Unreadable means resume is refused (FR-021) and the banner says the setting could not be read.

**Rationale**: siblings can't import `orchestrator.config`; reading one key keeps the dashboard from depending on the whole schedule's validation.

## D15. Market hours and the halt

**Decision**: the resume confirmation calls `risk.calendar.market_open(now)` (the same XNYS calendar the gate and Execution use, ADR 0013). An exception from it counts as "open" for the warning (FR-022). The halt comes from `system_state_effective.daily_loss_halt_active`, the view the gate's own computation defines, never recomputed.

## D16. `--check`

**Decision**: prints one line per item, `<name>: ok` or `<name>: FAILED <code>`, exit 0 only if all are ok:

| Line | Passes when |
|---|---|
| `database_read` | connects with `DASHBOARD_DATABASE_URL`, runs `SELECT 1`, and `pg_has_role` shows the login is a member of `ta_dashboard` and of no other `ta_*` role |
| `database_control` | connects with `DASHBOARD_CONTROL_DATABASE_URL` and is a member of `ta_dashboard_control` and of no other `ta_*` role; nothing is updated |
| `password_hash` | `DASHBOARD_PASSWORD_HASH` parses as D5's format with the expected parameters |
| `session_secret` | `DASHBOARD_SESSION_SECRET` is at least 32 bytes |
| `schedule` | D14 reads a boolean |
| `config` | `config/dashboard.yaml` loads (D17) |

A failure code names the check's reason (`wrong_role:<role>`, `unreachable`, `too_short`, `malformed`) or the exception type, never a value or database message (FR-031).

## D17. Configuration file

**Decision**: `config/dashboard.yaml`, every key required, unknown keys rejected, a bad file refusing start (exit 2), like `config/assistant.yaml`:

```yaml
owner_time_zone: Asia/Singapore
session_hours: 12
page_size: 50
sign_in_delay: {first_seconds: 1, cap_seconds: 30, tracked_addresses: 1000}
max_sessions: 20
database: {statement_timeout_seconds: 10}
```

## D18. Logs

**Decision**: stdout, one line per event, `dashboard <event> key=value …`:
- `started`, `stopped`;
- `sign_in ok`, `sign_in failed site_wait=<s> address_wait=<s>`, `sign_in too_soon`;
- `toggle paused|resumed`, `toggle refused reason=<run_while_paused|unreadable|stale|csrf>`;
- `error type=<ExceptionType> route=<path template>`.

Never: a password, hash, cookie, session or CSRF value, a connection string, a client address, stored model text or a database error message (FR-024, FR-029). uvicorn's access log is off, so query strings and addresses aren't logged. A marker-string test (as feature 013's) feeds secrets and model text through every route and asserts none reaches the captured logs.

## D19. Times

**Decision**: `timefmt.py` converts each aware timestamp per moment with `zoneinfo`: `19 Oct 09:12 SGT (18 Oct 21:12 ET)`, using `owner_time_zone` and `America/New_York`, so daylight-saving changes are right on both sides. Dates without times (trading days, journal) are NYSE days and are shown as such. Ages are "3 min old", "5 h old", "2 d old". Every page footer shows "Read at …".

## D20. Charts

**Decision**: `charts.py` turns `(time, value)` points into an SVG `polyline` in a fixed `viewBox` (e.g. 600×200), with min/max labels and the first and last times, all computed on the server. Equity curve ranges: `1d` (the current or last NYSE trading day), `1w`, `1m`, `all`; `1d` and `1w` use every snapshot, `1m` and `all` the last snapshot of each NYSE trading day (`DISTINCT ON ((taken_at AT TIME ZONE 'America/New_York')::date)`). The journal comparison (FR-010a) uses the same ranges over journal days: one polyline per analyst from `agents.<name>.index`, and an account line compounded from `account.return`, each rebased to 100 at its first point in range. A missed session (`missed_sessions`) is a marker; an unknown schema version or a null return breaks the line (a new polyline starts) rather than inventing a value. One point renders as a dot; none renders "No data for this range."

## D21. Attribution parsing

**Decision**: `attribution.py` parses `per_agent_attribution` against `specs/012-journal-writer/contracts/attribution.md` schema version 1: decimals from strings with `Decimal`, unknown keys ignored, missing required keys or another `schema_version` giving "attribution can't be displayed" for that row (FR-010). Usage totals (FR-010b) sum `usage` per agent over the range's rows; J9's windows don't overlap, so the sum counts each report once.

## D22. Migration 0015

**Decision**: `0015_trading_paused_changed_at.sql` (ADR 0027): add the nullable column; a `plpgsql` function and `BEFORE UPDATE` trigger on `system_state` that sets `NEW.trading_paused_changed_at = now()` when `NEW.trading_paused IS DISTINCT FROM OLD.trading_paused`; no backfill. Column privileges are checked against an `UPDATE`'s own `SET` list, not values a trigger changes, so no role needs a new grant; the integration test proves it by toggling as `ta_dashboard_control` and recording a baseline as `ta_risk_gate`.

## Things flagged

1. **D7's residual**: a sustained, scripted attacker can hold off the owner's sign-in while the attack lasts (no fixed lockout, but the race for each slot). The spec's edge case says the owner waits "at most 30 s"; that holds only without such an attacker. Recommended: accept, correct the spec's sentence, and rely on the 12-hour session, the runbook path and the failure log lines.
2. **The public address** is generated by the owner in Railway after `railway config apply` (its name isn't known before). Quickstart R5 checks that a later `railway config plan` does not propose removing it; if it does, the generated name is declared in `.railway/railway.ts` under `networking.serviceDomains`, and the guard test allows networking keys on `dashboard` only.
