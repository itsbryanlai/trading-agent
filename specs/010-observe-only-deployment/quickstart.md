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
- Execution refusing a paused buy as `trading_paused` with no broker call (existing spec 003 tests).

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

Execution isn't started in the rehearsal: it would need the real paper keys.

Tear down with `docker stop ta-rehearse`.

## 4. Railway plan, before anything is applied

```bash
railway config plan
```

**Expected**: create `postgres`, `orchestrator`, `risk-gate`, `reference-data` and `execution`, and nothing else. Values show as `«hidden»`.

## 5. Post-deploy check (owner, after the first trading day)

Run the "first trading day" block of `docs/operations/observe-queries.sql` as `ta_owner_read_login` (research R12). **Expected** (SC-002):
- paused is true;
- `positions` is empty;
- an account snapshot exists from today;
- every decision has a verdict;
- every approved buy has a `trading_paused` refusal;
- `orders` is empty.
