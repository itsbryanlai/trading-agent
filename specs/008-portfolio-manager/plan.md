# Implementation Plan: Portfolio Manager agent

**Branch**: `008-portfolio-manager` | **Date**: 2026-10-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-portfolio-manager/spec.md`

## Summary

`python -m trading_agent.portfolio_manager` runs when the orchestrator starts it: a morning session and event-driven runs (ADR 0011). One run:
1. reads, in one consistent snapshot, every unexpired report from both analysts, the positions, today's latest account snapshot, recent journal entries, and its own decisions from earlier today;
2. fetches a Finnhub quote for each symbol, with its own read-only key, and keeps only fresh ones;
3. makes one model call, to Qwen `qwen3.7-plus` by default or Anthropic Sonnet by configuration;
4. checks every proposed decision in code;
5. writes the valid decisions and their report links in one transaction.

The Risk Gate's own loop then evaluates each buy or sell decision within a minute, and rejects any whose quote is more than 15 minutes old.

**How it's built**:
- **Pure core**: building the model's input (research P6, P7) and checking its answer (P8). That makes "nothing the model invents is written" (SC-001, SC-003, SC-008) a provable property.
- **Three ports**: quotes (the reference job's existing Finnhub adapter, P3), the model (P5) and the store (P10), each faked in tests.
- **A shared `trading_agent.llm` package**: Research's model port and adapters move there unchanged, so both agents use one copy (P5).
- **The gate**: its loop gains decision evaluation, and its core gains `decision_stale` (P12), recorded in ADR 0019 first.
- **Migration 0012**: `decisions.quote_time`.
- **Orchestrator entry**: this feature enables `portfolio_manager` (P16).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: all existing: psycopg 3.2, PyYAML, exchange-calendars (through `trading_agent.risk.calendar`), the standard library's `urllib` (Finnhub, Qwen), `anthropic` 1.x (moved with its adapter, still imported by one module). No new dependency.

**Storage**: the shared Postgres. The PM inserts `decisions` and `decision_reports` as `ta_portfolio_manager`, with no new grant. Migration 0012 adds one column.

**Testing**: pytest with Hypothesis. Offline: fake quotes, model and store. Integration (`ta-pg`): the write, the reads, the gate's loop, the migration and the grants. No network (`tests/conftest.py`).

**Target Platform**: a Linux child process of the orchestrator on its Railway worker service; the gate's loop on its own service (ADR 0013). Neither deployed yet.

**Project Type**: an LLM agent in the existing `trading_agent` package, one run per process; plus a change to a deterministic service.

**Performance Goals**: fits the orchestrator's 10-minute PM timeout by construction (P11: the shipped defaults' worst case is 7.5 minutes). A decision has a verdict within about 60 seconds of being written (SC-006).

**Constraints**:
- **No broker credential, no `config/risk.yaml`, no verdicts or orders read.** The only writes are its own decisions and links.
- **Only `PORTFOLIO_MANAGER_*` variables** are read.
- **Model output is checked before it's written**, whatever the provider. Model-written text is never logged.
- **No automatic failover** between providers.

**Scale/Scope**: up to about 13 runs a day. Typically 5–20 unexpired reports on 3–15 symbols, up to about 12 positions, 0–10 decisions per run.

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | The PM has no broker credential and no path to one; its output is a `decisions` row the gate judges. The gate stays a pure function plus a deterministic loop: the new rule is a fixed comparison of two times, and the loop calls the existing idempotent `evaluate_decision`. No model call is added to the gate or Execution. | Pass |
| II. Analysts propose, PM decides | The PM fetches its own quote (P3) and portfolio state (P6), never reads `config/risk.yaml` (import guard), cites every report it draws on (`decision_reports`), and convergence never sizes mechanically: the prompt forbids it and the PM's size is its own (P7, P8). | Pass |
| III. Least privilege at the database | No new grant for the PM: its existing grants cover every read and write (data-model.md). The gate's loop uses `ta_risk_gate`'s existing `SELECT` on `decisions`. The gate's login never enters the PM's process (ADR 0019). An integration test asserts the PM still can't write anything else. | Pass |
| IV. Autonomous, one hard stop | No approval gate is added. `decision_stale` is a data-validity rule like `stop_loss_trigger_stale`, not an approval step, and the PM re-decides on its next run. | Pass |
| V. Spec and ADR first | ADR 0019 (accepted 2026-10-04) is in place before the gate's code or migration 0012 changes. `docs/specs/portfolio-manager-agent.md`, `docs/specs/data-model.md`, the gate's contracts and the architecture overview are updated after it (FR-026). No change to `config/risk.yaml`. | Pass |
| VI. Paper only, US equities | Not affected. The gate re-checks universe eligibility for every buy. | Pass |
| VII. Assistant and dashboard read-only | Not affected. They already read `decisions`; the new column and rule name are more to display. | Pass |
| Technology section (v1.1.0) | Qwen through QwenCloud's OpenAI-compatible API and Anthropic through the `anthropic` SDK, chosen in config. | Pass |

**Re-check after design**: all pass. The design adds one package (`llm`, a move), one config file, one column and one gate rule. It adds no table, no grant and no variable outside `PORTFOLIO_MANAGER_*`.

## Project Structure

### Documentation (this feature)

```text
specs/008-portfolio-manager/
├── spec.md
├── plan.md                    # this file
├── research.md                # P1–P16
├── data-model.md              # decisions rows, migration 0012, reads, in-memory entities
├── quickstart.md
├── contracts/
│   ├── pm-interface.md        # CLI, exit codes, env, config, answer schema, drop and skip reasons
│   ├── ports.md               # QuoteSource, ModelClient (trading_agent.llm), Store
│   └── gate-changes.md        # the gate's loop, decision_stale
├── checklists/requirements.md
└── tasks.md                   # /speckit-tasks

docs/adr/0019-the-gate-evaluates-pm-decisions-in-its-own-loop.md   # accepted 2026-10-04
```

### Source Code (repository root)

```text
src/trading_agent/
├── llm/                       # moved from research (P5), behaviour unchanged
│   ├── __init__.py
│   ├── ports.py               # ModelClient, ModelReply, model errors
│   ├── qwen.py
│   ├── anthropic_client.py
│   └── settings.py            # ModelSettings, parsing, provider variables, https check
├── research/                  # imports trading_agent.llm instead of its own copies
├── portfolio_manager/
│   ├── __init__.py
│   ├── __main__.py            # env, config, --dry-run, exit codes (P9)
│   ├── config.py              # config/portfolio_manager.yaml loader, cross-checks (P11)
│   ├── freshness.py           # pure: is a quote fresh (P4)
│   ├── inputs.py              # pure: reports → candidates, ids, evidence, size limit (P6, P7)
│   ├── prompt.py              # PROMPT_VERSION, system prompt, user document (P7)
│   ├── answer.py              # pure: ANSWER_SCHEMA, check(), drop reasons, rows (P8)
│   ├── store.py               # Store protocol, Inputs, PostgresStore (P10)
│   └── service.py             # one run (P1, P3, P9)
├── risk/
│   ├── model.py               # DecisionRequest.quote_time
│   ├── gate.py                # MAX_DECISION_QUOTE_AGE, decision_stale
│   ├── rules.py               # DECISION_STALE
│   ├── service.py             # reads decisions.quote_time
│   ├── runner.py              # evaluate_pending_decisions
│   └── __main__.py            # each pass: triggers, then decisions
└── storage/migrations/
    └── 0012_decision_quote_time.sql

config/portfolio_manager.yaml  # new
config/schedule.yaml           # portfolio_manager enabled, env listed (P16)
pyproject.toml                 # import-linter layers: llm, portfolio_manager

tests/
├── fakes/pm_store.py          # new; fakes/model.py and fakes/market_data.py reused
├── unit/llm/                  # moved from unit/research
├── unit/portfolio_manager/    # inputs, freshness, answer (tables + properties), prompt,
│                              # config, service, main, import guard
├── unit/risk/                 # decision_stale; rules contract
└── integration/
    ├── portfolio_manager/     # write, reads, role limits
    ├── risk/                  # the loop picks up decisions; decision_stale
    └── storage/               # 0012; grants unchanged; helpers gain quote_time

docs/specs/portfolio-manager-agent.md, docs/specs/data-model.md,
docs/architecture/overview.md, docs/policy/versioning.md,
specs/002-risk-gate/contracts/{gate-interface,rejection-rules}.md      # FR-026, ADR 0019
.env.example                                                            # PORTFOLIO_MANAGER_*
```

**Structure Decision**: a new `trading_agent.portfolio_manager` package in the top layer, built like `research`.
- **It may import** `trading_agent.llm`, `storage.db`, `reference.finnhub` and `reference.provider` (the quote adapter and its types), and `risk.calendar`.
- **It imports nothing** from `execution`, `orchestrator`, `research`, or `risk` other than `calendar`. An import-guard test enforces that, and that no PM module reads `config/risk.yaml`.

**Order of work** (each its own commits): ADR 0019 → the `llm` move (refactor, no behaviour change) → migration 0012 and test helpers → the gate's rule and loop → the PM's pure core → its store, service and entry point → config and orchestrator entry → docs.

## Things flagged for the owner

1. **A refactor of merged Research code** (research P5): its model port and both adapters move to a new `trading_agent.llm` package, so the PM shares them instead of copying about 300 lines of key-handling code. Behaviour, config and logs don't change; Research's tests prove it. It adds an `llm` layer to the import-linter configuration (beside `storage`, at the bottom). **The alternative** is a second copy inside `portfolio_manager`. Recommended: the move.
2. **Gate changes, which are order logic** (research P12, ADR 0019, `contracts/gate-changes.md`):
   - the gate's loop starts evaluating PM decisions; without it, decisions are inert;
   - a new rejection rule, `decision_stale`, at 15 minutes, as a code constant like the trigger's 10-minute limit rather than a `config/risk.yaml` key. It means a PM sell, including a full exit, can be rejected when its quote is old. Stop-loss exits are unaffected.

   **ADR 0019 accepted** by the owner on 2026-10-04, with the recommendations on items 1–4.
3. **Two spec refinements found while planning**, made in the spec alongside this plan:
   - **Quote freshness is 5 minutes, not 15** (P4): the gate's 15-minute rule counts from the quote's time, and the PM's run takes up to 7.5 minutes, so the PM must fetch fresher quotes for its decisions to arrive in time. The config loader enforces the arithmetic.
   - **A run where no symbol gets a fresh quote fails** (exit 1, `no_fresh_quotes`) instead of passing as quiet (P3). A rejected market-data key fails too. The spec's User Story 4 said such a run "succeeds"; that would hide a broken key or feed.
4. **"Everything dropped" exits 0.** The model answered and code refused every proposal; that's the checks working, logged reason by reason. If you'd rather see it as a failed run in the orchestrator's records, it's a one-line change.
5. **Prompts reach Alibaba's servers** with Qwen as the provider, carrying the paper account's positions, cash and equity. You chose this knowingly (Clarifications); noted again because it's the first agent whose prompt holds portfolio data.
6. **Not yet confirmed**; your try-out run (quickstart step 4) settles them: the real token use and cost; that Finnhub's `t` is the last trade's time for every symbol, not only AAPL (ADR 0016's observation).
7. **Before deploying**: migration 0012 needs the migration admin to own `decisions`, the same item already on your list for 0009 and 0011. The gate's service must be redeployed with this code, or decisions wait unevaluated.

## Complexity Tracking

No constitution violations to justify.
