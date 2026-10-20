# Quickstart: The Dashboard

Two parts: checks a developer runs offline and against a throwaway database, and the owner's release steps. Contracts: [dashboard-interface.md](contracts/dashboard-interface.md), [routes.md](contracts/routes.md), [toggle.md](contracts/toggle.md).

## Part 1: validation (developer)

From the feature worktree, with the venv linked and `PYTHONPATH=src`:

1. **Unit tests and lint.** `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/dashboard tests/unit/deploy tests/unit/storage tests/unit/assistant -q`, then `scripts/lint.sh` and `.venv/bin/ruff format --check src tests`. Expected: all pass, including the route-access table (SC-006), the hostile fixtures (SC-003), the template checks (no `<script>`, no `style=`, `|safe` only for Markdown), the CSP on every route, the control-URL guard, the log-marker test and the deployed-shape guard.
2. **Integration tests.** With a throwaway Postgres on 55432 (`docker run -d --name ta-test-pg-014 -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=test postgres:16`), `TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres PYTHONPATH=src .venv/bin/python -m pytest -m integration tests/integration/dashboard tests/integration/storage -q`. Expected: migration `0015`'s trigger rows (data-model.md) hold; every query in contracts/queries.md runs as `ta_dashboard`; the toggle's `UPDATE` works as `ta_dashboard_control` and is `42501` as `ta_dashboard`; a direct `UPDATE` of `trading_paused_changed_at` is `42501` for every role; the logins command has 13 rows; each query is timed on a seeded year (SC-007). Remove the container afterwards.
3. **Run it locally.** With the test database migrated and seeded, export the four `DASHBOARD_*` variables (a hash from `--hash-password`, a secret from `openssl rand -base64 48`), run `PYTHONPATH=src .venv/bin/python -m trading_agent.dashboard` with `PORT=8000`, and open `http://localhost:8000`. Browsers accept `Secure` cookies on `localhost`. Expected: sign-in, every view, pause, a refused resume while `run_while_paused: true`.

## Part 2: release (owner)

Type every secret step in your own terminal tab (runbook §1), never through the chat. These steps become runbook §10.

**R1. Migrate.** Release order matters: migrate before the code that needs the column is merged into `release/prod` (runbook §3 step 1). Enable the database's public proxy, export `ADMIN_DATABASE_URL` (with `sslmode=require`) with `read -rs`, run `PYTHONPATH=src .venv/bin/python -m trading_agent.storage.migrate`, then disable the proxy and unset the variable. Expected: `0015` applied; `SELECT trading_paused_changed_at FROM system_state` returns null.

**R2. Create the two logins.** With the proxy enabled and the admin URL exported again, run the logins command as in runbook §2 step 7 (`--service-host postgres.railway.internal`). It creates only `ta_dashboard_login` and `ta_dashboard_control_login` and prints each string once. Store them in your password manager, then disable the proxy and unset the variable.

**R3. Make the password hash and the session secret.** `PYTHONPATH=src .venv/bin/python -m trading_agent.dashboard --hash-password` (at least 16 characters, typed twice, never echoed) prints the hash. `openssl rand -base64 48` prints a secret (at least 32 bytes with at least 16 distinct values; a repeated character is refused as `too_short`). Keep both in your password manager.

**R4. Check.** With all four `DASHBOARD_*` values exported (the two database URLs through the public proxy with `sslmode=require` and the host override), run `PYTHONPATH=src .venv/bin/python -m trading_agent.dashboard --check`. All six lines must read `ok`. Anything else: don't deploy, and raise it.

**R5. Plan, apply, set values, make the address.** Merge `main` into `release/prod` (runbook §3). From a clean `release/prod` checkout, after `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/deploy -q` passes, run `railway config plan`. Read every line with the assistant: it must show one new service, `dashboard`, with a health check on `/healthz` and only the four `DASHBOARD_*` variables, and nothing else changed. Then `railway config apply`, and **at once** set the four values in the Railway dashboard (with none set it starts, exits and is restarted until its retries run out). Then, in the service's Settings → Networking, generate a domain. Run `railway config plan` again: it must show no changes. If it proposes removing the domain, stop and raise it (research, "Things flagged" 2).

**R6. First visit.** Open the generated address on your phone.
- Sign in. The banner must read "Observe-only. Trading paused since … start time not recorded …" (until the first toggle after `0015`), and say the PM still runs.
- Check Overview, Orders, Activity and Journal against `observe-queries.sql` blocks 1–5.
- Open "Resume trading": it must be refused, naming `run_while_paused: true` and runbook §5. `SELECT trading_paused FROM system_state` must still be `t`.
- Sign out, then press Back and reload: you must be asked to sign in.
- Address check (research D8): from your laptop, `curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-For: 203.0.113.9' -d password=wrong https://<address>/sign-in` twice, then once more with `203.0.113.10`, waiting 10 s before each repeat (an attempt inside the wait is answered "try again" and not counted). `railway logs -s dashboard --deployment --lines 10` must show `address_wait` growing on the third attempt (the forged header didn't make a new address). Wait 30 s, then sign in normally.
- `railway logs -s dashboard --deployment --lines 20` shows `started`, `sign_in` and `toggle refused` lines, and no address, password or text.

**R7. The Assistant.** In the private chat send "pause trading". The refusal must name "the dashboard's pause control" and the runbook sections, with no "(not built yet)". This needs the same release (the Assistant's text ships with it).

## Not part of this release

Switching trading on stays runbook §5's two steps. The dashboard's resume becomes available only after a separate, reviewed release sets `run_while_paused: false`; then the owner resumes from the dashboard after a close.
