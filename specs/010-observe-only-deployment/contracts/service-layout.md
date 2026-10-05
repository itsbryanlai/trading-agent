# Contract: Railway service layout (observe-only)

What `.railway/railway.ts` declares, and what each service may hold. The guard test (research R11) enforces the "Never" column.

| Service | Source | Start command | Variables (names only, all `preserve()`) | Never |
|---|---|---|---|---|
| `postgres` | Railway managed | — | (Railway's own) | — |
| `orchestrator` | `itsbryanlai/trading-agent`, branch `release/prod` | `python -m trading_agent.orchestrator` | `ORCHESTRATOR_DATABASE_URL`, `RESEARCH_DATABASE_URL`, `RESEARCH_FINNHUB_API_KEY`, `RESEARCH_DASHSCOPE_API_KEY`, `RESEARCH_QWEN_BASE_URL`, `RESEARCH_ANTHROPIC_API_KEY`¹, `PORTFOLIO_MANAGER_DATABASE_URL`, `PORTFOLIO_MANAGER_FINNHUB_API_KEY`, `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY`, `PORTFOLIO_MANAGER_QWEN_BASE_URL`, `PORTFOLIO_MANAGER_ANTHROPIC_API_KEY`¹ | `ALPACA_*`, `EXECUTION_*`, `ADMIN_DATABASE_URL`, any reference to the database's own variables |
| `risk-gate` | same | `python -m trading_agent.risk` | `RISK_GATE_DATABASE_URL` | as above, plus any model or market-data key |
| `reference-data` | same | `python -m trading_agent.reference` | `REFERENCE_DATA_DATABASE_URL`, `REFERENCE_DATA_FINNHUB_API_KEY` | as above, plus any model key |

¹ Declared but left unset while `model.provider: qwen`. The orchestrator passes only set names (spec 005), and the agent requires it only for the Anthropic provider.

Build, for every service: Railpack, with Python from `.python-version` (3.12) and dependencies from `requirements.txt` (`-e .`). One replica each, with Railway's default on-failure restart.

Every `*_DATABASE_URL` is a component login string printed by the login command ([logins-command.md](logins-command.md)), pointing at the database's private network host.

## After switch-on (not part of this feature's deployment)

| Service | Start command | Variables |
|---|---|---|
| `execution` | `python -m trading_agent.execution` | `EXECUTION_DATABASE_URL`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_BASE_URL` (optional, must be the paper address) |

Added only by the reviewed switch-on change and applied after the close (research R7, R8).
