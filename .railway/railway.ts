// The Railway project, as code (ADR 0021). Applied by the owner only:
//   railway config plan   # preview, values hidden
//   railway config apply
// Variable VALUES never appear here: every variable is preserve(), which keeps
// whatever value is already set on Railway. The logins come from
// `python -m trading_agent.storage.logins`; keys are set in the dashboard.
//
// What each service may hold is fixed by
// specs/010-observe-only-deployment/contracts/service-layout.md, and
// tests/unit/deploy/test_deployed_shape.py reads this file as text to enforce it.
// Keep one `NAME: preserve(),` per line, and spell the source exactly as below.
//
// Build: Railpack, Python 3.12 from .python-version. requirements.txt installs nothing:
// Railpack runs it before copying src/, so the editable install is the buildCommand.
// Every service starts from the repository root, on one replica, with Railway's
// default on-failure restart.

import { defineRailway, github, postgres, preserve, project, service } from "railway/iac";

export default defineRailway(() => {
  const db = postgres("postgres");

  const orchestrator = service("orchestrator", {
    source: github("itsbryanlai/trading-agent", { branch: "release/prod" }),
    build: { builder: "RAILPACK", buildCommand: "/app/.venv/bin/pip install -e ." },
    start: "python -m trading_agent.orchestrator",
    replicas: 1,
    env: {
      ORCHESTRATOR_DATABASE_URL: preserve(),
      RESEARCH_DATABASE_URL: preserve(),
      RESEARCH_FINNHUB_API_KEY: preserve(),
      RESEARCH_DASHSCOPE_API_KEY: preserve(),
      RESEARCH_QWEN_BASE_URL: preserve(),
      RESEARCH_ANTHROPIC_API_KEY: preserve(),
      PORTFOLIO_MANAGER_DATABASE_URL: preserve(),
      PORTFOLIO_MANAGER_FINNHUB_API_KEY: preserve(),
      PORTFOLIO_MANAGER_DASHSCOPE_API_KEY: preserve(),
      PORTFOLIO_MANAGER_QWEN_BASE_URL: preserve(),
      PORTFOLIO_MANAGER_ANTHROPIC_API_KEY: preserve(),
    },
  });

  const riskGate = service("risk-gate", {
    source: github("itsbryanlai/trading-agent", { branch: "release/prod" }),
    build: { builder: "RAILPACK", buildCommand: "/app/.venv/bin/pip install -e ." },
    start: "python -m trading_agent.risk",
    replicas: 1,
    env: {
      RISK_GATE_DATABASE_URL: preserve(),
    },
  });

  const referenceData = service("reference-data", {
    source: github("itsbryanlai/trading-agent", { branch: "release/prod" }),
    build: { builder: "RAILPACK", buildCommand: "/app/.venv/bin/pip install -e ." },
    start: "python -m trading_agent.reference",
    replicas: 1,
    env: {
      REFERENCE_DATA_DATABASE_URL: preserve(),
      REFERENCE_DATA_FINNHUB_API_KEY: preserve(),
    },
  });

  // The only service that holds broker keys (Constitution I, III). Observe-only:
  // trading is paused before this service first starts (ADR 0021).
  const execution = service("execution", {
    source: github("itsbryanlai/trading-agent", { branch: "release/prod" }),
    build: { builder: "RAILPACK", buildCommand: "/app/.venv/bin/pip install -e ." },
    start: "python -m trading_agent.execution",
    replicas: 1,
    env: {
      EXECUTION_DATABASE_URL: preserve(),
      ALPACA_API_KEY_ID: preserve(),
      ALPACA_API_SECRET_KEY: preserve(),
      ALPACA_BASE_URL: preserve(),
    },
  });

  return project("trading-agent", {
    resources: [db, orchestrator, riskGate, referenceData, execution],
  });
});
