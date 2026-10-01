# Quickstart: Orchestrator

How to validate feature 005. Contracts: [orchestrator-interface.md](contracts/orchestrator-interface.md), [launcher-port.md](contracts/launcher-port.md). Schema: [data-model.md](data-model.md).

## 1. Offline suite

```bash
.venv/bin/python -m pytest tests/ -q
```

The new `tests/unit/orchestrator/` tests cover:
- **The planner**: a full simulated trading day; weekends and holidays; an early close; event-driven spacing, wait and cutoff; the pause; catch-up; no backfill; Hypothesis properties for SC-003, SC-004 and SC-006.
- **The config loader**, including the ADR 0011 bounds and the prefix rule.
- **The service**, with a fake launcher: timeouts, overlap skips, interrupted runs.
- **The real launcher**, with the stand-in `tests/fakes/agent.py` child processes: exit status, timeouts that kill the process group, and an environment that holds only the allowed names.
- **The process**: exit codes, and that it reads only `ORCHESTRATOR_DATABASE_URL`.

## 2. Integration suite

```bash
TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres .venv/bin/python -m pytest tests/integration -m integration -q
```

| Scenario | Expected |
|---|---|
| `ta_orchestrator` permissions | It can read `latest_report_time` and `system_state.trading_paused`, insert run records, and update only `finished_at`, `outcome` and `detail`. It can't read `reports`, `decisions`, other `system_state` columns, or `system_state_effective`. The grants test passes both ways |
| The view | It returns the newest report time, and nothing else |
| Duplicate slot | A second record for the same agent, day, reason and slot is rejected |
| Restart | `running` rows become `interrupted`, and no slot is started twice |
| Single instance | A second orchestrator is refused while the first holds the lock |

## 3. Lint

```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

## Running

The orchestrator runs as its own process, and its only variable of its own is its database login:

```bash
ORCHESTRATOR_DATABASE_URL=... .venv/bin/python -m trading_agent.orchestrator
```

All agents ship disabled, so it starts nothing until an agent feature enables its entry in `config/schedule.yaml`. Each agent's variables (for example `RESEARCH_*`) must be set on the same service. No deployment config exists yet. Whichever feature writes the Railway config should start this process with Execution, the gate's runner and the reference-data job.
