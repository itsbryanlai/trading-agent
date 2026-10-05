# Quickstart: Validating the Observe-Only Deployment

Contracts: [service-layout.md](contracts/service-layout.md), [logins-command.md](contracts/logins-command.md), [observe-setting.md](contracts/observe-setting.md). Decisions: [research.md](research.md).

The full owner runbook (accounts, secrets, flattening the paper account, the release checklist, switch-on and switch-off) is a deliverable, kept in `docs/operations/deployment.md`. This page proves the feature works.

## 1. Offline suite and lint

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
PYTHONPATH=src scripts/lint.sh
```

**Expected**: all pass, including:
- the observe setting's loader and planner tests;
- the login command's unit tests;
- the deployed-shape guard test.

## 2. Integration suite

```bash
TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres PYTHONPATH=src .venv/bin/python -m pytest tests/integration -m integration -q
```

**Expected**: all pass, including:
- the login command scenarios;
- every statement of `observe-queries.sql` running as `ta_dashboard`;
- the gate rejecting a buy as `trading_paused` while paused, and Execution refusing a paused buy as a second layer (existing tests).

## 3. Rehearsal on a throwaway local database (Railway's Postgres major version)

```bash
docker run --rm -d --name ta-rehearse -e POSTGRES_PASSWORD=dev -p 5434:5432 postgres:<railway major>
export ADMIN_DATABASE_URL=postgresql://postgres:dev@localhost:5434/postgres
.venv/bin/python -m trading_agent.storage.migrate      # all versions, exit 0
.venv/bin/python -m trading_agent.storage.migrate      # nothing applied, exit 0 (SC-004)
.venv/bin/python -m trading_agent.storage.logins --service-host localhost:5434
.venv/bin/python -m trading_agent.storage.logins --service-host localhost:5434   # every line "exists, unchanged"
```

Then, as `ta_owner_control_login`, set the pause and read it back.

Start the orchestrator, gate and reference-data job locally, **from the repository root**, each with only its printed login and its contract's variables. Confirm each one:
- refuses to start, naming the variable, when that variable is unset (SC-005);
- runs when it is set. The gate loads `config/risk.yaml`, and the orchestrator logs `portfolio_manager runs while paused (observe-only, ADR 0021)`.

Rehearsed 2026-10-05 on `postgres:18` (Railway's image is `postgres-ssl:18`), with no API keys set. What to expect:
- The orchestrator logs the observe-only line, then starts a Research catch-up run, which refuses for its missing `RESEARCH_FINNHUB_API_KEY` (expected without keys). It stops cleanly on SIGTERM.
- The gate logs nothing at INFO. It is running if it is still up after a pass (60 seconds); a missing or bad `config/risk.yaml` ends it.
- The reference-data job needs `REFERENCE_DATA_FINNHUB_API_KEY` as well as its login, so it refuses (exit 2) without a key. That is the expected result here.
- Run the "pre-open" block as `ta_owner_read_login` after setting the pause: both rows read true on a fresh database.

Execution isn't started in the rehearsal: it would need the real paper keys.

Tear down with `docker stop ta-rehearse`.

## 4. Railway plan, before anything is applied

```bash
railway config plan
```

**Expected**: create `postgres`, `orchestrator`, `risk-gate`, `reference-data` and `execution`, and nothing else. Values show as `«hidden»`.

## 5. Pre-open check (owner, right after the first deploy)

Run the "pre-open" block of `observe-queries.sql` as `ta_owner_read_login` before the next open. **Expected**: paused is true, and `positions` is empty. Also re-confirm zero open orders in Alpaca. If either fails, remove `execution` from `.railway/railway.ts` and apply before the open.

## 6. Post-deploy check (owner, after the first trading day)

Run the "first trading day" block of `docs/operations/observe-queries.sql` as `ta_owner_read_login` (research R12). **Expected** (SC-002):
- paused is true;
- `positions` is empty;
- an account snapshot exists from today;
- every decision has a verdict;
- no buy verdict is approved, and every buy rejection reads `trading_paused`, `market_closed` or `decision_stale`;
- `orders` is empty.
