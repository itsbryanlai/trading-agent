# Quickstart: The Risk Gate counts orders still in flight

No keys and no network. CI runs the same three on every PR.

## 1. Offline tests

```bash
.venv/bin/python -m pytest tests/ -q
```

**Expected:** all pass, including the spec's scenarios with exact quantities and the properties in research I9: no change with nothing in flight, sells within what's available, cash above the reserve, a met target never ordered twice, stop-loss unchanged.

## 2. Integration tests (Docker `ta-pg` on port 5433)

```bash
TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres .venv/bin/python -m pytest tests/integration -m integration -q
```

**Expected:** all pass, including migration 0013's view for each order state, the one new grant, and two same-target decisions where the second is `target_already_met`.

## 3. Lint

```bash
scripts/lint.sh
```

```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```
