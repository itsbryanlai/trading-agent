# Quickstart: Validating the Observe-Only Deployment

Contracts: [service-layout.md](contracts/service-layout.md), [logins-command.md](contracts/logins-command.md). Decisions: [research.md](research.md).

The full owner runbook (accounts, secrets, release checklist, switch-on and switch-off) is a deliverable of this feature, kept in `docs/operations/deployment.md`. This page only proves the feature works.

## 1. Offline suite and lint

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
PYTHONPATH=src scripts/lint.sh
```

**Expected**: all pass, including:
- the login command's unit tests;
- the observe-only guard test (research R11): no `execution` service, no `ALPACA_`, `EXECUTION_` or `ADMIN_` name, no reference to the database's own variables, and each service declares only its contract's names.

## 2. Integration suite

```bash
TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres PYTHONPATH=src .venv/bin/python -m pytest tests/integration -m integration -q
```

**Expected**: all pass, including the login command scenarios listed in its contract.

## 3. Rehearsal on a throwaway local database (Railway's Postgres major version)

```bash
docker run --rm -d --name ta-rehearse -e POSTGRES_PASSWORD=dev -p 5434:5432 postgres:<railway major>
export ADMIN_DATABASE_URL=postgresql://postgres:dev@localhost:5434/postgres
.venv/bin/python -m trading_agent.storage.migrate      # all versions, exit 0
.venv/bin/python -m trading_agent.storage.migrate      # nothing applied, exit 0 (SC-004)
.venv/bin/python -m trading_agent.storage.logins --service-host localhost:5434
.venv/bin/python -m trading_agent.storage.logins --service-host localhost:5434   # every line "exists, unchanged"
```

Start each service locally with only its printed login and its contract's variables, then confirm that each one:
- refuses to start, naming the variable, when that variable is unset (SC-005);
- runs when it is set: the gate's loop and the reference-data job log passes, and the orchestrator logs its plan for the day.

Tear down with `docker stop ta-rehearse`.

## 4. Railway plan, before anything is applied

```bash
railway config plan
```

**Expected**: create `postgres`, `orchestrator`, `risk-gate` and `reference-data`, and nothing else. No `execution` service. Variable values show as `«hidden»`.

## 5. Observe one trading day (owner, after deploying)

With `ta_owner_read_login`, run the read queries from the runbook. Expected after a full trading day (SC-002):
- reports from Research;
- decisions from the PM, each with exactly one `risk_verdicts` row;
- zero rows in `orders` and `execution_refusals`.

## 6. Switch-on rehearsal (on the local throwaway database, before doing it for real)

- Insert an approved verdict for today, then run Execution with a fake broker at a time after the close. **Expected**: refused `approval_expired`, no order (research R7, SC-006).
- Point `ALPACA_BASE_URL` at a non-paper address. **Expected**: Execution exits 2 (FR-012).
