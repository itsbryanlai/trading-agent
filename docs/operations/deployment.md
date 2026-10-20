# Deployment runbook (owner)

Who this is for: the owner. Every step here is done by you, on your own machine or in the Railway and Alpaca dashboards. No service runs any of it. Decisions: [ADR 0021](../adr/0021-railway-deployment-as-code-observe-only-first.md). The Railway project is defined in [`.railway/railway.ts`](../../.railway/railway.ts) and explained in [`.railway/README.md`](../../.railway/README.md). Checks: [`observe-queries.sql`](observe-queries.sql).

This runbook uses variable names only. Never paste a value into a file in this repository, a commit, a chat or a ticket. The one place values live is Railway's variable store, and your password manager.

Observe-only means: Execution is deployed, trading is paused before any service starts, and the paper account is flat before Execution first starts. The Portfolio Manager runs while paused (`portfolio_manager.run_while_paused: true`), so you see real decisions and verdicts, and no buy is ever approved.

## 1. What you need

Accounts and keys, by name:

| You need | For |
|---|---|
| A Railway account and a project | the four services and the Postgres |
| A GitHub repository with a `release/prod` branch | the deploy source (`itsbryanlai/trading-agent`) |
| An Alpaca **paper** account: `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` | Execution only |
| A Finnhub key per use: `RESEARCH_FINNHUB_API_KEY`, `PORTFOLIO_MANAGER_FINNHUB_API_KEY`, `REFERENCE_DATA_FINNHUB_API_KEY`, `JOURNAL_FINNHUB_API_KEY` | market data (read-only) |
| A Qwen key and endpoint: `RESEARCH_DASHSCOPE_API_KEY`, `RESEARCH_QWEN_BASE_URL`, `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY`, `PORTFOLIO_MANAGER_QWEN_BASE_URL` | the models (`RESEARCH_ANTHROPIC_API_KEY` and `PORTFOLIO_MANAGER_ANTHROPIC_API_KEY` only if a provider is switched to Anthropic) |
| A password manager | the eight database login strings, printed once |

On your machine:
- Python 3.12 and this repository's virtual environment (`.venv`), for `migrate` and `logins`. Run them from the repository root with `PYTHONPATH=src`.
- `psql`, to run the SQL in this runbook. On macOS: `brew install libpq`, then put it on your `PATH` (it is keg-only): `echo 'export PATH="$(brew --prefix libpq)/bin:$PATH"' >> ~/.zshrc`, and open a new terminal tab. `psql --version` should then work.
- The Railway CLI, logged in and linked to the project, plus Node, for `railway config plan` and `railway config apply`. In `.railway/`, run `npm install` once.

The variable names each service holds are listed in [`.env.example`](../../.env.example), grouped by service.

### Working with secrets in the terminal

Lessons from the first deploy (2026-10-06):

- **Type these commands directly in your terminal tab. Never run them through an assistant's `!` shell-escape**, or anything else that captures output: their output contains credentials (the `logins` command prints eight passwords). A `!` command also runs in a separate shell, so variables you set in your tab are empty there. If credentials do leak, rotate them with `logins --reset <name>` before using them.
- **Read a connection string without echoing it** (zsh, the macOS default shell; bash's `read -rs NAME` form fails in zsh with "not an identifier"). Run this on its own, paste at the silent prompt, and press Enter:

  ```bash
  read -rs "ADMIN_DATABASE_URL?Paste the URL: "
  ```

  Check it is set without printing it: `echo ${#ADMIN_DATABASE_URL}` must show a number, not `0`. Type `${NAME}` literally in later commands; it refers to the variable.
- **Point a login string at the public proxy without editing it.** Login strings carry the private host. Query parameters override it, and the first one starts with `?`:

  ```bash
  export CONTROL_URL="${CONTROL_URL}?sslmode=require&host=PROXY_HOST&port=PROXY_PORT"
  ```

  `PROXY_HOST` is the host alone and `PROXY_PORT` the number alone, both from the `postgres` service's Public Networking settings. An error such as `could not translate host name "postgres.railway.internal"` means the override is missing. `invalid connection option "?sslmode"` means the variable was empty when you appended to it.
- **Read-only checks can run in Railway's own query editor** (the `postgres` service → **Database** → **Query**), with no proxy and no pasted credential. That editor connects as the admin user, so run only the `SELECT` blocks of [`observe-queries.sql`](observe-queries.sql) there. Every write in this runbook (the pause) goes through `ta_owner_control_login`.

## 2. First deploy, in this exact order

Do not reorder. Execution is created early but cannot start until step 11 gives it keys, and by then the pause is set (step 8) and the paper account is flat (step 10).

1. **Start from an empty Railway project.** The project `trading-agent` / `production` must hold no services. A database added by hand (for example one named `Postgres`) is not the one `.railway/railway.ts` declares (`postgres`), and `plan` would delete it. If one exists from earlier testing and holds nothing you need, delete it and its volume in the dashboard. That is permanent, so check first.
2. **Create `release/prod` from `main`** on GitHub, once this feature is merged into `main`. Services deploy only from this branch. Then add GitHub **branch protection** on `release/prod`: changes by pull request only, with the lint, unit and integration checks required to pass before a merge.
3. **Guard, then plan.** Run `plan` and `apply` only from a **clean checkout of `release/prod`** (no local edits), after the guard test passes there:

   ```bash
   PYTHONPATH=src .venv/bin/python -m pytest tests/unit/deploy -q
   ```

   Then, from that checkout's repository root, linked to the project (`railway link`: `trading-agent`, `production`, and skip the service prompt):

   ```bash
   railway config plan
   ```

   The plan must read **`5 to add, 0 to change, 0 to destroy`**: create database `postgres`, and create services `orchestrator`, `risk-gate`, `reference-data` and `execution`, with every value hidden. If it would change or destroy anything, stop and don't apply. (A dry run on 2026-10-06 confirmed that `preserve()` on variables not yet set is accepted.)
4. **Apply:**

   ```bash
   railway config apply
   ```

   The database starts. The four services build and then refuse to start (exit 2), because none has its values yet. That is expected. Execution cannot start without its keys, and it gets them last (step 11).
5. **Enable the database's public TCP proxy** in the `postgres` service's networking settings. It stays on only until step 9. Read the proxied connection string (`DATABASE_PUBLIC_URL`, from the `postgres` service's variables) into your shell only, as `ADMIN_DATABASE_URL`, with `read -rs` (see *Working with secrets in the terminal*). Never put it in a file or on a Railway service. **Every connection you make over the public proxy must add `sslmode=require`** (append `?sslmode=require` to the string, or `&sslmode=require` if it already has a query), including this one, the pause in step 8 and every check later in this runbook.
6. **Run `migrate`** from your machine:

   ```bash
   PYTHONPATH=src .venv/bin/python -m trading_agent.storage.migrate
   ```

   Exit 0 and every migration applied. Running it again applies nothing.
7. **Run `logins`**, with the database's **private** network host (shown in the `postgres` service's networking settings) as `--service-host`:

   ```bash
   PYTHONPATH=src .venv/bin/python -m trading_agent.storage.logins --service-host <private host>
   ```

   **Type this one directly in your terminal tab, never through a `!` shell-escape:** it prints eight connection strings once and writes nothing to disk. **Store every printed string in your password manager now.** They cannot be shown again. A lost one is replaced with `--reset <login name>`, which sets a new password for that login only. Re-running the command changes nothing for existing logins.
8. **Set the pause**, connected as `ta_owner_control_login`. Read its string from step 7 into `CONTROL_URL` with `read -rs`, then add `?sslmode=require&host=…&port=…` for the public proxy from step 5 (see *Working with secrets in the terminal*), and run `psql "$CONTROL_URL"`. Run:

   ```sql
   UPDATE system_state SET trading_paused = true, updated_at = now();
   ```

   Read it back:

   ```sql
   SELECT trading_paused FROM system_state;
   ```

   It must show `t`. Do not continue otherwise.
9. **Disable the public TCP proxy** in the `postgres` service's networking settings, and unset `ADMIN_DATABASE_URL` in your shell.
10. > ## ⚠️ REMINDER: CLOSE EVERY POSITION AND CANCEL EVERY OPEN ORDER IN ALPACA
    >
    > **In the Alpaca paper account, in Alpaca's own interface, close every position and cancel every open order. Then confirm the account shows zero positions and zero open orders.**
    >
    > Do this **before Execution gets its keys (step 11).** Execution reconciles broker positions when it starts, and it places stop-loss exits and approved sells even while trading is paused. A position left in the account could be sold during observation. Only you do this step. No component of this system closes positions or cancels orders for you.
11. **Set each service's values** in the Railway dashboard. Names are fixed by the contract and match `.env.example`. Do `execution` **last**, and only after steps 8 and 10:
    - `orchestrator`: `ORCHESTRATOR_DATABASE_URL`, `RESEARCH_DATABASE_URL`, `RESEARCH_FINNHUB_API_KEY`, `RESEARCH_DASHSCOPE_API_KEY`, `RESEARCH_QWEN_BASE_URL`, `PORTFOLIO_MANAGER_DATABASE_URL`, `PORTFOLIO_MANAGER_FINNHUB_API_KEY`, `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY`, `PORTFOLIO_MANAGER_QWEN_BASE_URL` (and the two `*_ANTHROPIC_API_KEY` names only when a provider needs them).
    - `risk-gate`: `RISK_GATE_DATABASE_URL`.
    - `reference-data`: `REFERENCE_DATA_DATABASE_URL`, `REFERENCE_DATA_FINNHUB_API_KEY`.
    - `execution`, last: `EXECUTION_DATABASE_URL`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, and `ALPACA_BASE_URL` left **empty** or set to exactly the Alpaca paper address. Any other address and Execution refuses to start.

    Each `*_DATABASE_URL` is the matching login string from step 7 (host: the database's private network host). Each service's variables are for that service only: do not copy a broker key anywhere but `execution`.

    After setting a service's values, redeploy it so it starts with them (its refused starts may have used up Railway's restart retries). Research may start a catch-up run as soon as the orchestrator is up. That is expected.
12. **Confirm Execution started on the paper account.** Open `execution`'s deploy logs. It must log a successful paper-account verification and keep running. If it exits, see section 7.
13. **Before the next market open, run the pre-open check.** The simplest way is Railway's query editor (the `postgres` service → **Database** → **Query**), with no proxy needed. Alternatively, re-enable the public proxy and connect as `ta_owner_read_login` (with `sslmode=require` and the host override). Run block 9 ("PRE-OPEN CHECK") of [`observe-queries.sql`](observe-queries.sql). Both rows must read `ok = true`: `trading_paused` is true and `positions` is empty. Re-confirm in Alpaca, in its own interface, that there are **zero positions and zero open orders** (Execution does not import orders it did not place).

    If you used the public proxy, disable it again.

    **If any check fails, or Alpaca shows any position or open order, keep the pause and leave Execution deployed.** Close or cancel it in Alpaca's own interface, and re-run this check until it passes. If `trading_paused` is false, set it (step 8) before anything else. Execution places stop-loss exits and approved sells even while paused, so do not leave a position open through the open: closing or cancelling it first is the remedy. Removing Execution is a switch-off, with its own conditions (section 6).

## 3. Release checklist

For every later release:

1. If the release adds a migration: enable the database's public proxy, export `ADMIN_DATABASE_URL` in your shell (with `sslmode=require`), run `migrate`, **then** disable the proxy and unset the variable. Migrate before the code that needs it is merged.
2. Open a pull request from `main` into `release/prod`. CI must be green.
3. Merge it. Railway redeploys the services from `release/prod`.
4. If `.railway/` changed: from a clean checkout of `release/prod`, after `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/deploy -q` passes, run `railway config plan`, read it, then `railway config apply`.
5. If the release changes the assistant, run its `--check` (section 9, step 3) with the release's values before merging; every line must read `ok`.
6. If the release changes the dashboard, run its `--check` (section 10, step 4) with the release's values before merging; every line must read `ok`.
7. Watch each service's logs start cleanly (section 7 if one doesn't).

## 4. Observing, and the post-deploy check

Use Railway's query editor (`postgres` → **Database** → **Query**; `SELECT`s only, since it runs as the admin user), or connect as `ta_owner_read_login` with the public proxy enabled for the session and disabled again afterwards. Over the public proxy, add `sslmode=require` and the host override. `ta_owner_read_login` can read, never write. Run the blocks of [`observe-queries.sql`](observe-queries.sql):

- Blocks 1 to 8 are everyday observation: reports, decisions with the reports they cite, each decision's verdict and reason, decisions with no verdict, account snapshots, Execution's refusals, orchestrator runs, and today's `instrument_reference` count.
- **After the first full trading day**, run block 10 ("FIRST TRADING DAY CHECK"). Every row must read `ok = true`: paused, no positions, a snapshot from today, at least one decision, every decision with a verdict, no approved buy, every buy rejection reading `trading_paused`, `market_closed` or `decision_stale`, and no orders.

If a row reads false, do not switch trading on. Find the cause first.

## 5. Switching trading on

Only after the first-trading-day check passes. Two steps, in this order, because the other order leaves a window where re-pausing would not stop the Portfolio Manager's decisions.

1. **While still paused**, release a reviewed change that sets `portfolio_manager.run_while_paused: false` in `config/schedule.yaml` and updates the deployed-shape guard test (`tests/unit/deploy/test_deployed_shape.py`) to match. Release it through `release/prod` (section 3), and confirm the orchestrator's log no longer prints `portfolio_manager runs while paused`.
2. **After the close and before the next open**, clear the pause. The preferred way is the dashboard's "Resume trading" (section 10), which is available once the orchestrator has skipped a Portfolio Manager run for the pause: after step 1 is released, the next morning session is skipped, then you resume after that day's close ([ADR 0028](../adr/0028-dashboard-resume-needs-the-orchestrator-to-have-skipped-a-pm-run.md)). It refuses while `run_while_paused` is still `true` or before that skipped run exists. A resume during the session is an emergency fallback only: the dashboard allows it with a warning, but the instruction stays "after the close and before the next open" ([ADR 0028](../adr/0028-dashboard-resume-needs-the-orchestrator-to-have-skipped-a-pm-run.md) §4). The manual statement below stays as a fallback and **bypasses the dashboard's guard**, so check step 1 yourself first. As `ta_owner_control_login` (over the public proxy, with `sslmode=require`):

   ```sql
   UPDATE system_state SET trading_paused = false, updated_at = now();
   ```

   Read it back:

   ```sql
   SELECT trading_paused FROM system_state;
   ```

   It must show `f`. Doing this after the close means no approval made while observing can become an order. A pause at any later time stops new buys again, and (with `run_while_paused` false) stops the Portfolio Manager too.

## 6. Switching trading off

**Pause** (the usual way). As `ta_owner_control_login` (over the public proxy, with `sslmode=require`):

```sql
UPDATE system_state SET trading_paused = true, updated_at = now();
```

Read it back with `SELECT trading_paused FROM system_state;`. Buys stop at once. Sells and stop-loss exits continue, because exits are never blocked by the pause.

**Remove Execution** (to stop it entirely): delete the `execution` service from `.railway/railway.ts`, then, from a clean checkout of `release/prod` with the guard test passing, `railway config plan` and `railway config apply`. Do this only when **all** of the following hold, checked as `ta_owner_read_login`:

- It is after the close.
- No positions:

  ```sql
  SELECT count(*) FROM positions;
  ```
- No open orders:

  ```sql
  SELECT count(*) FROM orders WHERE status IN ('submitted', 'partially_filled');
  ```
- Nothing in flight:

  ```sql
  SELECT count(*) FROM in_flight_orders;
  ```

All three must be `0`. Also confirm zero positions and zero open orders in Alpaca. If any is not zero, pause instead and let the exits finish.

## 7. If something breaks

- **Exit code 2**: a service refused to start. Its log says why and names the variable (a missing or invalid one), never the value. Execution also exits 2 if `ALPACA_BASE_URL` is not the paper address or the account is not paper. Fix the variable in Railway. Do not work around the refusal.
- **Exit code 3**: the database was unreachable or the connection was lost. Railway restarts the service. If it keeps happening, check the database service's status and the service's `*_DATABASE_URL` host and login.
- **Where to look**: each service's logs in the Railway dashboard (or `railway logs`); the orchestrator's run records (block 7 of `observe-queries.sql`) for what ran, what was skipped and why; Execution's refusals (block 6) for approvals it declined.
- **Not sure it is safe**: pause (section 6). It is always available and always reversible.

## 8. Adding the journal service (feature 012, ADR 0022)

The `journal` service is a cron job: it runs once after each weekday's close, writes one `journal` row and exits. Decision: [ADR 0022](../adr/0022-journal-writer-runs-after-the-close-with-its-own-finnhub-key.md). Behavior: [`docs/specs/journal.md`](../specs/journal.md). There is no migration. Type every secret step directly in your own terminal tab (section 1).

1. **Create the login.** Release the code first (section 3), then enable the database's public proxy, export `ADMIN_DATABASE_URL`, and run `logins` as in step 7 of section 2. It creates only the missing login, `ta_journal_login`, and prints its string once. Store it in your password manager, then disable the proxy and unset the variable.
2. **Get a read-only Finnhub key** for the journal, or reuse the shared account's (it then shares that account's rate limit, [ADR 0016](../adr/0016-market-data-for-the-llm-agents.md) §5). Export both values locally with `read -rs`, for steps 3 and 4 only.
3. **Check the quote after a close.** After 16:05 ET on a session day, with symbols of your choosing:

   ```bash
   PYTHONPATH=src .venv/bin/python -m trading_agent.journal --check AAPL MSFT
   ```

   Run it at the cron's time, 22:30 UTC, not only just after the close. Each line must show `t` inside today's session and `accepted: true` for liquid symbols; this is a release gate. If `t` falls after the close (`reason: after_close`), the scheduled run would fail as `no_prices`: do not release, and raise it.
4. **Dry run.** The same evening, with `JOURNAL_DATABASE_URL` pointing at the database through the proxy (with `sslmode=require` and the host override), run `python -m trading_agent.journal --dry-run`. It prints the would-be row and writes nothing.
5. **Plan and apply.** From a clean checkout of `release/prod`, after the guard test passes, run `railway config plan`. It must show one new service, `journal`, with the cron schedule `30 0,22 * * *` (22:30 UTC and a 00:30 UTC retry slot), restart policy `NEVER` and only `JOURNAL_DATABASE_URL` and `JOURNAL_FINNHUB_API_KEY`. Then `railway config apply`, and set the two values in the Railway dashboard.

The service runs whether or not trading is paused. A failed run is never restarted: read the log (the exit code names the reason, section 7), fix the cause and run it again by hand the same evening. The 00:30 UTC start is a second attempt and does nothing if the first wrote. Running it again by hand for a session that has a row does nothing; `--replace` rewrites that session's row.

## 9. Adding the assistant service (feature 013, ADRs 0023-0025)

The `assistant` service is always on: it long-polls Telegram, reads the database through a fixed query menu and answers the owner's private chat. Decisions: [ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md), [ADR 0024](../adr/0024-assistant-explains-refusals-and-reads-deployed-settings.md), [ADR 0025](../adr/0025-assistant-model-flag-adds-refusals.md). Behavior: [`docs/specs/assistant-agent.md`](../specs/assistant-agent.md). There is no migration. Type every secret step directly in your own terminal tab (section 1).

1. **Create the bot and find the chat ID.** Create a bot with @BotFather, message it from your private chat, and with the token exported (`read -rs`) run `PYTHONPATH=src .venv/bin/python -m trading_agent.assistant --find-chat-id`. Note the `chat.id` shown with `chat.type=private`: it is `ASSISTANT_TELEGRAM_CHAT_ID`. Do this before the service is deployed.
2. **Create the login and get the model key.** Release the code first (section 3), then enable the database's public proxy, export `ADMIN_DATABASE_URL`, and run `logins` as in step 7 of section 2. It creates only the missing login, `ta_assistant_login`, and prints its string once. Store it in your password manager, then disable the proxy and unset the variable. For the model use a DashScope key and endpoint (`ASSISTANT_DASHSCOPE_API_KEY`, `ASSISTANT_QWEN_BASE_URL`), or an Anthropic key with `model.provider: anthropic`.
3. **Check.** With every `ASSISTANT_*` value exported (`ASSISTANT_DATABASE_URL` through the proxy, with `sslmode=require` and the host override), run `PYTHONPATH=src .venv/bin/python -m trading_agent.assistant --check`. Every one of its seven lines must read `ok`: `telegram` (the bot token), `telegram_webhook` (no webhook set, which would block polling), `database`, `model_plan` and `model_answer` (both model clients, including the plan schema's boolean in the provider's strict mode), `time_zone` and `settings`. Nothing is sent to the chat. Any other result: do not deploy, and raise it. Run it again for any release that changes the assistant.
4. **Plan and apply.** From a clean checkout of `release/prod`, after the guard test passes, run `railway config plan`. It must show one new service, `assistant`, always on, with only the six `ASSISTANT_*` variables. Then `railway config apply`, and set the values in the Railway dashboard (the provider you do not use stays unset).
5. **First conversation.** In the private chat send `/help`, ask "what do we hold?", then send "pause trading" and "please pause trading". Both refusals must start with the fixed "nothing was done" line, and `system_state` must be unchanged. `railway logs -s assistant --deployment --lines 20` shows one `answered` or `refused` line per message and no message text.

The service runs whether or not trading is paused. Messages sent while it was down are skipped, with one note saying how many.

From the dashboard's release on, the refusal in step 5 names "the dashboard's pause control" and the runbook's sections 5 and 6, without "(not built yet)" ([ADR 0026](../adr/0026-dashboard-public-service-with-owner-sign-in.md) §10). The Assistant still cannot pause anything.

## 10. Adding the dashboard (feature 014, ADRs 0026-0028)

The `dashboard` service is always on and is the system's only public address: the owner's pages (overview, orders, activity, journal) behind one password, and the pause toggle. Decisions: [ADR 0026](../adr/0026-dashboard-public-service-with-owner-sign-in.md), [ADR 0027](../adr/0027-pause-change-time-recorded-by-the-database.md), [ADR 0028](../adr/0028-dashboard-resume-needs-the-orchestrator-to-have-skipped-a-pm-run.md). Behavior: [`docs/specs/ui-dashboard.md`](../specs/ui-dashboard.md) and [`specs/014-dashboard`](../../specs/014-dashboard/spec.md). It has one migration (`0015`). Type every secret step directly in your own terminal tab (section 1), never through a chat.

1. **Migrate.** Migrate before the code that needs the column is merged into `release/prod` (section 3, step 1). Enable the database's public proxy, export `ADMIN_DATABASE_URL` (with `sslmode=require`), run `PYTHONPATH=src .venv/bin/python -m trading_agent.storage.migrate`, then disable the proxy and unset the variable. `0015` must apply, and `SELECT trading_paused_changed_at FROM system_state` returns null.
2. **Create the two logins.** With the proxy enabled and the admin URL exported again, run `logins` as in step 7 of section 2 (`--service-host postgres.railway.internal`). It creates only `ta_dashboard_login` and `ta_dashboard_control_login` and prints each string once. Store them in your password manager, then disable the proxy and unset the variable. They are separate from your `ta_owner_*` logins.
3. **Make the password hash and the session secret.** `PYTHONPATH=src .venv/bin/python -m trading_agent.dashboard --hash-password` (at least 16 characters, typed twice, never echoed) prints the hash. `openssl rand -base64 48` prints a secret of at least 32 bytes. Keep both in your password manager.
4. **Check.** With all four `DASHBOARD_*` values exported (the two database URLs through the public proxy, with `sslmode=require` and the host override), run `PYTHONPATH=src .venv/bin/python -m trading_agent.dashboard --check`. All six lines (`database_read`, `database_control`, `password_hash`, `session_secret`, `schedule`, `config`) must read `ok`: each login is the exact expected login, holds no `CREATEROLE`, `CREATEDB`, `SUPERUSER` or `BYPASSRLS`, and is a member of its one group role only; the hash is `scrypt` with exactly n=32768, r=8, p=1. Nothing is written. Any other result: do not deploy, and raise it. Run it again for any release that changes the dashboard (section 3, step 6).
5. **Plan, apply, set values, make the address.** Merge `main` into `release/prod` (section 3). From a clean `release/prod` checkout, after `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/deploy -q` passes, run `railway config plan`. It must show one new service, `dashboard`, with a health check on `/healthz` and only the four `DASHBOARD_*` variables, and nothing else changed. Then `railway config apply`, and **at once** set the four values in the Railway dashboard (with none set it starts, exits and is restarted until its retries run out). Then, in the service's Settings, Networking, generate a domain. Run `railway config plan` again: it must show no changes. If it proposes removing the domain, stop and raise it.
6. **First visit.** Open the generated address on your phone.
   - Sign in. The banner must read "Observe-only. Trading paused since …" (or "start time not recorded" until the first toggle after `0015`) and say the PM still runs.
   - Check Overview, Orders, Activity and Journal against blocks 1 to 5 of [`observe-queries.sql`](observe-queries.sql).
   - Open "Resume trading": it must be refused, naming `run_while_paused: true` and section 5. `SELECT trading_paused FROM system_state` must still be `t`.
   - Sign out, press Back and reload: you must be asked to sign in.
   - Address check: from your laptop run `curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-For: 203.0.113.9' -d password=wrong https://<address>/sign-in` twice, then once more with `203.0.113.10`, **waiting 10 s before each repeat** (an attempt inside the wait is answered "try again" and not counted). `railway logs -s dashboard --deployment --lines 10` must show `address_wait` growing on the third attempt (the forged header must not make a new address). Wait 30 s, then sign in normally.
   - `railway logs -s dashboard --deployment --lines 20` shows `started`, `sign_in` and `toggle refused` lines, and no address, password or text.
7. **The Assistant.** In the private chat send "pause trading". The refusal must name "the dashboard's pause control" and the runbook sections, with no "(not built yet)". The Assistant's text ships in the same release.

The service runs whether or not trading is paused. Under a sustained attack on the sign-in, your own sign-in can be held off while the attack lasts ("Try again in N seconds"); existing sessions keep working for about 12 hours, failures show in the service's logs, and section 5's manual statement remains a way to set the pause.

Switching trading on stays section 5's two steps. The dashboard's resume becomes available only after a separate, reviewed release sets `run_while_paused: false` and the orchestrator has then skipped a Portfolio Manager run for the pause (ADR 0028); you then resume from the dashboard after a close.
