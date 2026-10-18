# Contract: routes

Every route the dashboard serves. Anything not listed is `404`. Research: D4, D7, D10, D11, D20.

## Access rule

| Route | Session needed | Without one |
|---|---|---|
| `GET /sign-in`, `POST /sign-in` | no | — |
| `GET /healthz` | no | — |
| `GET /static/style.css` | no | — |
| every other `GET` | yes | `303` to `/sign-in` |
| every other `POST` | yes, plus the CSRF value and a same-origin or absent `Origin` | `403` "Request refused. Nothing was changed." |

A unit test builds the app, enumerates its route table and checks each route against this table; a new route without an entry here fails it.

## Routes

| Method, path | Query / form | Response |
|---|---|---|
| `GET /sign-in` | — | the form; if already signed in, `303 /` |
| `POST /sign-in` | `password` | ok: set cookie, `303 /`. Wrong: `200` form with "Sign-in failed." Too soon: `429` form with "Too many attempts. Try again in N seconds." |
| `POST /sign-out` | `csrf` | delete session, clear cookie, `303 /sign-in` |
| `GET /` | `range` = `1d` \| `1w` \| `1m` \| `all` (default `1w`) | overview: state, halt, banner, positions, cash and equity, equity curve |
| `GET /orders` | `symbol`, `from`, `to`, `page` | order history (FR-008) |
| `GET /activity` | `agent` = `research` \| `opportunistic_identifier` \| `portfolio_manager` \| `runs`, `problems` = `1`, `symbol`, `from`, `to`, `page` | reports, decisions and runs, merged newest first (FR-009) |
| `GET /journal` | `range`, `page` | comparison chart, usage totals, day rows (FR-010) |
| `GET /toggle` | — | the confirmation page for the one available action (contracts/toggle.md) |
| `POST /toggle` | `csrf`, `expected` = `paused` \| `running`, `target` = the other | write or refuse, then `303 /toggle/done?result=…` |
| `GET /toggle/done` | `result` = `paused` \| `resumed` \| `stale` \| `refused` \| `failed` | the outcome page; it states the current state read fresh, not the parameter alone |
| `GET /healthz` | — | `200` `ok`, or `503` `unavailable`; `text/plain`, nothing else |
| `GET /static/style.css` | — | the stylesheet |

Query validation: an unknown `range`, `agent` or `problems` value falls back to the default; `from`/`to` must be ISO dates, otherwise ignored; `symbol` is upper-cased, at most 10 characters of `A–Z`, `.` and `-`, otherwise ignored; `page` is an integer from 1, otherwise 1. A filter matching nothing, or a page past the end, is an empty list (never an error).

## Headers on every response

```text
Content-Security-Policy: default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Strict-Transport-Security: max-age=31536000
Cache-Control: no-store          (except /static/*)
```

## Session cookie

`__Host-ta_dashboard=<id>.<signature>; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=43200`. Cleared on sign-out with `Max-Age=0`.

## Every signed-in page

- The observe-only banner while paused (FR-011), e.g. "Observe-only. Trading paused since 6 Oct 09:12 SGT (5 Oct 21:12 ET). The PM still runs (run_while_paused: true), and the gate rejects every buy."
- The halt line while the halt is active: "Daily-loss halt active: no new orders until the next trading day."
- Navigation: Overview, Orders, Activity, Journal, and the toggle button ("Pause trading" or "Resume trading").
- A sign-out form.
- Footer: "Read at 19 Oct 09:30 SGT (18 Oct 21:30 ET)."
- No `<script>`, no `style=` attribute, no outside resource.

## Errors

| Situation | Response |
|---|---|
| Read login fails | `503` "Database unavailable. Nothing is shown until it is back." |
| A template or parsing bug | `500` "Something went wrong." with no detail |
| Unknown route | `404` "Not found." |
