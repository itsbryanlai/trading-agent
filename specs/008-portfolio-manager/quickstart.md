# Quickstart: Portfolio Manager agent

How to check that feature 008 works. Steps 1–3 need no keys and touch no network. Step 4 is the owner's, with real keys: it reads the database, fetches quotes and makes one model call (about $0.01 on Qwen, research Cost), and writes nothing.

## 1. Offline tests

```bash
.venv/bin/python -m pytest tests/ -q
```

**Expected:** all pass, including every drop reason, every run outcome, the config cross-checks, the injection round-trip, the SC-001, SC-003 and SC-008 properties, `decision_stale` in the gate's core, and Research's tests after the move to `trading_agent.llm`.

## 2. Integration tests (Docker `ta-pg` on port 5433)

```bash
TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres .venv/bin/python -m pytest tests/integration -m integration -q
```

**Expected:** all pass. That includes:
- migration 0012: a decision without `quote_time` is rejected;
- the PM's write as `ta_portfolio_manager`: decisions and links together, all or nothing;
- the gate's loop: today's buy gets one verdict within a pass, a hold none, yesterday's is left alone, a quote over 15 minutes old is `decision_stale`;
- the unchanged grants matrix.

## 3. Lint

```bash
scripts/lint.sh
```

```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

**Expected:** clean, including the new `llm` layer and `portfolio_manager` in the top layer.

## 4. Owner: a try-out run (no writes)

During market hours, with a database that holds some reports (for example after a Research run), set these in your own Terminal tab: `PORTFOLIO_MANAGER_DATABASE_URL`, `PORTFOLIO_MANAGER_FINNHUB_API_KEY`, `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY` and `PORTFOLIO_MANAGER_QWEN_BASE_URL` (the endpoint matching the key, as in [007's quickstart](../007-research-agent/quickstart.md#4-owner-a-try-out-run-no-writes)). Then run:

```bash
.venv/bin/python -m trading_agent.portfolio_manager --dry-run
```

**Check:**
- Each symbol considered prints with its quote, quote time and current weight; any skipped symbol prints with its reason.
- The would-be decisions print. Each cites report ids, and every buy cites a buy report.
- The token use and input size print. Compare them with research's cost estimate.

## 5. Enabling it

This feature sets `portfolio_manager.enabled: true` in `config/schedule.yaml`. Nothing runs until the orchestrator and the gate's loop are deployed, a later item. On the orchestrator's service, set the five `PORTFOLIO_MANAGER_*` variables.
