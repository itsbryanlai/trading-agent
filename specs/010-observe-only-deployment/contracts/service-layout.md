# Contract: Railway service layout

What `.railway/railway.ts` declares, and what each service may hold. The guard test (research R11) enforces it.

| Service | Start command | Variables (names only, all `preserve()`) | Never |
|---|---|---|---|
| `postgres` | Railway managed | (Railway's own) | — |
| `orchestrator` | `python -m trading_agent.orchestrator` | `ORCHESTRATOR_DATABASE_URL`, `RESEARCH_DATABASE_URL`, `RESEARCH_FINNHUB_API_KEY`, `RESEARCH_DASHSCOPE_API_KEY`, `RESEARCH_QWEN_BASE_URL`, `RESEARCH_ANTHROPIC_API_KEY`¹, `PORTFOLIO_MANAGER_DATABASE_URL`, `PORTFOLIO_MANAGER_FINNHUB_API_KEY`, `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY`, `PORTFOLIO_MANAGER_QWEN_BASE_URL`, `PORTFOLIO_MANAGER_ANTHROPIC_API_KEY`¹ | `ALPACA_*`, `EXECUTION_*`, `ADMIN_DATABASE_URL`, database-owned variables |
| `risk-gate` | `python -m trading_agent.risk` | `RISK_GATE_DATABASE_URL` | as above, plus any model or market-data key |
| `reference-data` | `python -m trading_agent.reference` | `REFERENCE_DATA_DATABASE_URL`, `REFERENCE_DATA_FINNHUB_API_KEY` | as above, plus any model key |
| `execution` | `python -m trading_agent.execution` | `EXECUTION_DATABASE_URL`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_BASE_URL`² | any other component's names, `ADMIN_DATABASE_URL`, database-owned variables |
| `journal` | `python -m trading_agent.journal` | `JOURNAL_DATABASE_URL`, `JOURNAL_FINNHUB_API_KEY` | `ALPACA_*`, `EXECUTION_*`, any model key, `ADMIN_DATABASE_URL`, database-owned variables |

¹ Declared but left unset while `model.provider: qwen`. The orchestrator passes only set names (spec 005).
² Optional. If set, it must be exactly the paper address, or Execution refuses to start.

**Every service**:
- **Source**: `github("itsbryanlai/trading-agent", { branch: "release/prod" })`.
- **Build**: Railpack, Python from `.python-version` (3.12), and `buildCommand: "/app/.venv/bin/pip install -e ."`. `requirements.txt` installs nothing: Railpack runs it before copying `src/`, so the editable install has to wait for the build step (first deploy, 2026-10-07).
- **Run**: working directory at the repository root (the gate and Execution read `config/risk.yaml` relative to it). One replica, and Railway's default on-failure restart (except the journal, below).

Every `*_DATABASE_URL` is a login string printed by the login command ([logins-command.md](logins-command.md)), pointing at the database's private network host.

**The journal** (feature 012, [ADR 0022](../../../docs/adr/0022-journal-writer-runs-after-the-close-with-its-own-finnhub-key.md)) is the one scheduled service: `deploy: { cronSchedule: "30 0,22 * * *", restartPolicyType: "NEVER" }`. No other service has a cron schedule or a restart policy, and the guard test enforces both. It runs, writes one `journal` row and exits; it starts at 22:30 UTC and again at 00:30 UTC, the same New York evening, and the second does nothing if the first wrote or the evening is not a session; a failed run is re-run by hand.

**Deploy order on the first deploy** (research R9): pause set, then the paper account flat, then `railway config apply`. Execution must never start against an unpaused database or a non-flat account.
