# Contract: Railway service layout

What `.railway/railway.ts` declares, and what each service may hold. The guard test (research R11) enforces it.

| Service | Start command | Variables (names only, all `preserve()`) | Never |
|---|---|---|---|
| `postgres` | Railway managed | (Railway's own) | — |
| `orchestrator` | `python -m trading_agent.orchestrator` | `ORCHESTRATOR_DATABASE_URL`, `RESEARCH_DATABASE_URL`, `RESEARCH_FINNHUB_API_KEY`, `RESEARCH_DASHSCOPE_API_KEY`, `RESEARCH_QWEN_BASE_URL`, `RESEARCH_ANTHROPIC_API_KEY`¹, `PORTFOLIO_MANAGER_DATABASE_URL`, `PORTFOLIO_MANAGER_FINNHUB_API_KEY`, `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY`, `PORTFOLIO_MANAGER_QWEN_BASE_URL`, `PORTFOLIO_MANAGER_ANTHROPIC_API_KEY`¹ | `ALPACA_*`, `EXECUTION_*`, `ADMIN_DATABASE_URL`, database-owned variables |
| `risk-gate` | `python -m trading_agent.risk` | `RISK_GATE_DATABASE_URL` | as above, plus any model or market-data key |
| `reference-data` | `python -m trading_agent.reference` | `REFERENCE_DATA_DATABASE_URL`, `REFERENCE_DATA_FINNHUB_API_KEY` | as above, plus any model key |
| `execution` | `python -m trading_agent.execution` | `EXECUTION_DATABASE_URL`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_BASE_URL`² | any other component's names, `ADMIN_DATABASE_URL`, database-owned variables |

¹ Declared but left unset while `model.provider: qwen`. The orchestrator passes only set names (spec 005).
² Optional. If set, it must be exactly the paper address, or Execution refuses to start.

**Every service**:
- **Source**: `github("itsbryanlai/trading-agent", { branch: "release/prod" })`.
- **Build**: Railpack, Python from `.python-version` (3.12), dependencies from `requirements.txt` (`-e .`).
- **Run**: working directory at the repository root (the gate and Execution read `config/risk.yaml` relative to it). One replica, and Railway's default on-failure restart.

Every `*_DATABASE_URL` is a login string printed by the login command ([logins-command.md](logins-command.md)), pointing at the database's private network host.

**Deploy order on the first deploy** (research R9): pause set, then the paper account flat, then `railway config apply`. Execution must never start against an unpaused database or a non-flat account.
