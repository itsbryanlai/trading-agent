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
| A Finnhub key per use: `RESEARCH_FINNHUB_API_KEY`, `PORTFOLIO_MANAGER_FINNHUB_API_KEY`, `REFERENCE_DATA_FINNHUB_API_KEY` | market data (read-only) |
| A Qwen key and endpoint: `RESEARCH_DASHSCOPE_API_KEY`, `RESEARCH_QWEN_BASE_URL`, `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY`, `PORTFOLIO_MANAGER_QWEN_BASE_URL` | the models (`RESEARCH_ANTHROPIC_API_KEY` and `PORTFOLIO_MANAGER_ANTHROPIC_API_KEY` only if a provider is switched to Anthropic) |
| A password manager | the eight database login strings, printed once |

On your machine:
- Python 3.12 and this repository's virtual environment (`.venv`), for `migrate` and `logins`. Run them from the repository root with `PYTHONPATH=src`.
- `psql`, to run the SQL in this runbook.
- The Railway CLI, logged in and linked to the project, plus Node, for `railway config plan` and `railway config apply`. In `.railway/`, run `npm install` once.

The variable names each service holds are listed in [`.env.example`](../../.env.example), grouped by service.

## 2. First deploy, in this exact order

Do not reorder. Steps 5 and 7 make the system safe before Execution can start.

1. **Create the Railway project and its Postgres.** Create the project in the Railway dashboard and add a Postgres database service named `postgres`. Create nothing else by hand: `railway config apply` creates the four services (step 9).
2. **Enable the database's public TCP proxy** in the database service's networking settings. It stays on only until step 6. Export the proxied connection string in your shell only, as `ADMIN_DATABASE_URL` (the superuser's string, from the database service's variables). Never put it in a file or on a Railway service. **Every connection you make over the public proxy must add `sslmode=require`** (append `?sslmode=require` to the string, or `&sslmode=require` if it already has a query), including this one, the pause in step 5 and every check later in this runbook.
3. **Run `migrate`** from your machine:

   ```bash
   PYTHONPATH=src .venv/bin/python -m trading_agent.storage.migrate
   ```

   Exit 0 and every migration applied. Running it again applies nothing.
4. **Run `logins`**, with the database's **private** network host (shown in the database service's networking settings) as `--service-host`:

   ```bash
   PYTHONPATH=src .venv/bin/python -m trading_agent.storage.logins --service-host <private host>
   ```

   It prints eight connection strings once and writes nothing to disk. **Store every printed string in your password manager now.** They cannot be shown again. A lost one is replaced with `--reset <login name>`, which sets a new password for that login only. Re-running the command changes nothing for existing logins.
5. **Set the pause**, connected as `ta_owner_control_login` (use its string from step 4, but with the **public** host from step 2, since you are outside Railway's network, and `sslmode=require` added). Run:

   ```sql
   UPDATE system_state SET trading_paused = true, updated_at = now();
   ```

   Read it back:

   ```sql
   SELECT trading_paused FROM system_state;
   ```

   It must show `t`. Do not continue otherwise.
6. **Disable the public TCP proxy** in the database service's networking settings, and unset `ADMIN_DATABASE_URL` in your shell.
7. > ## ⚠️ REMINDER: CLOSE EVERY POSITION AND CANCEL EVERY OPEN ORDER IN ALPACA
   >
   > **In the Alpaca paper account, in Alpaca's own interface, close every position and cancel every open order. Then confirm the account shows zero positions and zero open orders.**
   >
   > Do this **before Execution can ever start.** Execution reconciles broker positions when it starts, and it places stop-loss exits and approved sells even while trading is paused. A position left in the account could be sold during observation. Only you do this step. No component of this system closes positions or cancels orders for you.
8. **Create `release/prod` from `main`** (GitHub: a branch from `main`). Services deploy only from this branch. Then add GitHub **branch protection** on `release/prod`: changes by pull request only, and the lint, unit and integration checks required to pass before a merge.
9. **Plan, check, apply.** Run `plan` and `apply` only from a **clean checkout of `release/prod`** (no local edits), after the guard test passes there:

   ```bash
   PYTHONPATH=src .venv/bin/python -m pytest tests/unit/deploy -q
   ```

   Then, from that checkout's repository root, linked to the project:

   ```bash
   railway config plan
   ```

   Check the output: it creates exactly `orchestrator`, `risk-gate`, `reference-data` and `execution`, and nothing else, with every value hidden. `postgres` already exists from step 1 and must **not** be listed for creation. If the plan would create a second database, or delete or change anything, stop and don't apply.

   **At this first `plan`, also check that `preserve()` on variables not yet set on Railway is accepted.** If the plan rejects those variables, set them in the Railway dashboard first (step 10, creating each service's variables), then run `plan` again.

   Then:

   ```bash
   railway config apply
   ```

   The four services start building at once. Without their values they refuse to start (exit 2), and that is expected. Execution in particular cannot start without its keys.
10. **Set each service's values** in the Railway dashboard (or paste them into its variables page). Names are fixed by the contract and match `.env.example`:
    - `orchestrator`: `ORCHESTRATOR_DATABASE_URL`, `RESEARCH_DATABASE_URL`, `RESEARCH_FINNHUB_API_KEY`, `RESEARCH_DASHSCOPE_API_KEY`, `RESEARCH_QWEN_BASE_URL`, `PORTFOLIO_MANAGER_DATABASE_URL`, `PORTFOLIO_MANAGER_FINNHUB_API_KEY`, `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY`, `PORTFOLIO_MANAGER_QWEN_BASE_URL` (and the two `*_ANTHROPIC_API_KEY` names only when a provider needs them).
    - `risk-gate`: `RISK_GATE_DATABASE_URL`.
    - `reference-data`: `REFERENCE_DATA_DATABASE_URL`, `REFERENCE_DATA_FINNHUB_API_KEY`.
    - `execution`: `EXECUTION_DATABASE_URL`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, and `ALPACA_BASE_URL` left **empty** or set to exactly the Alpaca paper address. Any other address and Execution refuses to start.

    Each `*_DATABASE_URL` is the matching login string from step 4 (host: the database's private network host). Each service's variables are for that service only: do not copy a broker key anywhere but `execution`.

    Then redeploy each service, so it starts with its values (a refused start may have used up Railway's restart retries).
11. **Confirm Execution started on the paper account.** Open `execution`'s deploy logs. It must log a successful paper-account verification and keep running. If it exits, see section 7.
12. **Before the next market open, run the pre-open check.** Re-enable the database's public TCP proxy, connect as `ta_owner_read_login` with its string on the public host (with `sslmode=require`), and run block 9 ("PRE-OPEN CHECK") of [`observe-queries.sql`](observe-queries.sql). Both rows must read `ok = true`: `trading_paused` is true and `positions` is empty. Re-confirm in Alpaca, in its own interface, that there are **zero positions and zero open orders** (Execution does not import orders it did not place).

    Then disable the public proxy again.

    **If any check fails, or Alpaca shows any position or open order, keep the pause and leave Execution deployed.** Close or cancel it in Alpaca's own interface, and re-run this check until it passes. If `trading_paused` is false, set it (step 5) before anything else. Execution places stop-loss exits and approved sells even while paused, so do not leave a position open through the open: closing or cancelling it first is the remedy. Removing Execution is a switch-off, with its own conditions (section 6).

## 3. Release checklist

For every later release:

1. If the release adds a migration: enable the database's public proxy, export `ADMIN_DATABASE_URL` in your shell (with `sslmode=require`), run `migrate`, **then** disable the proxy and unset the variable. Migrate before the code that needs it is merged.
2. Open a pull request from `main` into `release/prod`. CI must be green.
3. Merge it. Railway redeploys the services from `release/prod`.
4. If `.railway/` changed: from a clean checkout of `release/prod`, after `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/deploy -q` passes, run `railway config plan`, read it, then `railway config apply`.
5. Watch each service's logs start cleanly (section 7 if one doesn't).

## 4. Observing, and the post-deploy check

Connect as `ta_owner_read_login`, with the public proxy enabled for the session and disabled again afterwards (or from inside Railway's network). Over the public proxy, add `sslmode=require`. It can read, never write. Run the blocks of [`observe-queries.sql`](observe-queries.sql):

- Blocks 1 to 8 are everyday observation: reports, decisions with the reports they cite, each decision's verdict and reason, decisions with no verdict, account snapshots, Execution's refusals, orchestrator runs, and today's `instrument_reference` count.
- **After the first full trading day**, run block 10 ("FIRST TRADING DAY CHECK"). Every row must read `ok = true`: paused, no positions, a snapshot from today, at least one decision, every decision with a verdict, no approved buy, every buy rejection reading `trading_paused`, `market_closed` or `decision_stale`, and no orders.

If a row reads false, do not switch trading on. Find the cause first.

## 5. Switching trading on

Only after the first-trading-day check passes. Two steps, in this order, because the other order leaves a window where re-pausing would not stop the Portfolio Manager's decisions.

1. **While still paused**, release a reviewed change that sets `portfolio_manager.run_while_paused: false` in `config/schedule.yaml` and updates the deployed-shape guard test (`tests/unit/deploy/test_deployed_shape.py`) to match. Release it through `release/prod` (section 3), and confirm the orchestrator's log no longer prints `portfolio_manager runs while paused`.
2. **After the close and before the next open**, as `ta_owner_control_login` (over the public proxy, with `sslmode=require`), clear the pause:

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
