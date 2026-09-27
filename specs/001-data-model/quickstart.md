# Quickstart: Validating the Shared Data Model

How to prove this feature works end-to-end. Schema details: [data-model.md](data-model.md).
Permissions: [contracts/role-grants.md](contracts/role-grants.md). Views:
[contracts/views.md](contracts/views.md).

## Prerequisites

- Python 3.12, and the project installed with its dev dependencies (`pip install -e ".[dev]"`)
- Docker, for a disposable local Postgres 16
- No broker, model, or news credentials — this feature touches none

## 1. Start a throwaway database

```bash
docker run --rm -d --name ta-pg -e POSTGRES_PASSWORD=dev -p 5433:5432 postgres:16
export ADMIN_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres
export TEST_DATABASE_URL=$ADMIN_DATABASE_URL
```

The test database must be disposable: the suite creates cluster-wide `ta_*` roles. Never point
`TEST_DATABASE_URL` at the Railway database.

## 2. Apply migrations

```bash
python -m trading_agent.storage.migrate
```

**Expected**: each migration version printed once, then exit 0. Run it a second time: nothing is
applied, exit 0 (idempotent).

## 3. Run the suite

```bash
python -m pytest tests/ -q                                 # offline; needs no database
python -m pytest tests/integration -m integration -q       # against TEST_DATABASE_URL
```

**Expected**: all pass. With `TEST_DATABASE_URL` unset, the integration suite reports *skipped*
with a message naming the variable — not failed.

What the integration suite proves, mapped to the spec:

| Check | Spec |
|---|---|
| Every cell of the grants matrix, as the actual role via `SET ROLE` — allowed succeeds, everything else rejected | FR-002, 005, 007, 009, 010, 014, 016, 017; SC-002 |
| Research can't insert an `opportunistic_identifier` row and vice versa | FR-002; US1 scenario 2 |
| A `no_action` report persists with no symbol | FR-001; US1 scenario 3 |
| Past-expiry report reads `expired`; cited report reads `consumed`; neither was updated | FR-003; US1 scenario 4 |
| Report → decision → verdict → order chain joins with no gaps | SC-001; US2 scenario 1–3 |
| Order against a rejected verdict fails; second verdict for a decision fails; duplicate `{day}-{symbol}-{side}` fails | FR-006, 008; US2 scenario 4; Edge Cases |
| Risk Gate and Execution cannot `SELECT` from `journal` | FR-012; US3 scenario 3 |
| Halt set "yesterday" reads inactive today with zero writes | FR-015; US4 scenario 3; SC-005 |
| Risk Gate can't touch `trading_paused`; dashboard control can't touch halt columns | FR-014; US4 scenario 2 |

## 4. Manual spot-check (optional)

```bash
psql "$ADMIN_DATABASE_URL" -c "\dp reports"                       # grants and RLS policies
psql "$ADMIN_DATABASE_URL" -c "SELECT * FROM system_state_effective"
```

No local `psql`? Use the container's: `docker exec ta-pg psql -U postgres -c "\dp reports"`.

**Expected**: `trading_paused = f`, `daily_loss_halt_active = f`, baseline NULL on a fresh
database.

## 5. Provision a real component login (production, once per component)

Run by an operator against the Railway database with the admin credential, never committed:

```sql
CREATE ROLE ta_research_login LOGIN PASSWORD '<generated>' IN ROLE ta_research;
```

Repeat per group role in contracts/role-grants.md. Each component receives only its own login's
connection string.

## Teardown

```bash
docker stop ta-pg
```
