# Implementation Plan: Risk Gate

**Branch**: `002-risk-gate` | **Date**: 2026-09-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-risk-gate/spec.md`

## Summary

A deterministic checkpoint that turns each Portfolio Manager decision, or each stop-loss trigger
from Execution's monitor, into exactly one recorded verdict: approved with a fully-specified order,
or rejected naming one rule. The core is a pure function over plain values and a validated config
(`config/risk.yaml`). It uses exact decimal arithmetic, target-weight sizing at the buy price
ceiling, and a fixed rule precedence. A thin service loads the inputs as `ta_risk_gate` and persists
the verdict plus any halt or baseline intent in one transaction, serialized by an advisory lock so
the daily order cap can't race. Migration `0006` adds `stop_loss_triggers`, `instrument_reference`,
a `ta_reference_data` role, and lets a verdict come from a trigger. Decisions: [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `psycopg[binary]` 3.2 (existing); new: `PyYAML` 6.0 (config),
`exchange-calendars` 4.13 (XNYS market hours; brings pandas, numpy), and `hypothesis` 6.x (dev,
property tests)

**Storage**: PostgreSQL 16, extended by one forward-only migration (`0006_risk_gate.sql`)

**Testing**: pytest. Offline unit and property tests (Hypothesis, ≥10,000 examples for the
invariants) need no database. The integration suite reuses feature 001's harness.

**Target Platform**: Linux container on Railway (the worker service); the gate runs in-process

**Project Type**: Single Python project; adds the `trading_agent.risk` package

**Performance Goals**: None meaningful. There are a handful of evaluations per hour, each a few
queries and microseconds of arithmetic.

**Constraints**: The core is pure, with no I/O, network, model or environment reads (FR-002). No
broker or market-data credential anywhere in the feature. Decimal arithmetic only. The config
fails closed.

**Scale/Scope**: 1 migration, 2 new tables, 1 new role, 1 altered table; ~15 rules; 2 service entry
points

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this design satisfies it |
|---|---|---|
| I. Deterministic Trade Path (NON-NEGOTIABLE) | PASS | The core is a pure function (G1) with decimal arithmetic (G2). The gate is the only producer of `approved_order`. Stop-loss exits also pass through it (G12), and it re-derives entry and line itself (G13). No credential of any kind. |
| II. Analysts Propose, PM Decides | PASS | The gate judges only PM decisions, never reports. The PM never reads `config/risk.yaml`, enforced by an import-scan test (G15). |
| III. Least Privilege at the Database | PASS | `ta_risk_gate` gains only `SELECT` on the two new tables. The new `ta_reference_data` role writes only `instrument_reference`. The grants matrix is amended and still tested for exact equality. |
| IV. Autonomous Operation, One Hard Stop | PASS | The daily-loss halt is recorded from the gate's own evaluation. It never liquidates, and exits pass every stop but market closed (G5). The pause is honored defensively (FR-020). |
| V. Spec-and-ADR-First | PASS | ADR 0010 and ADR 0011 precede this plan. The new tables and role are in ADR 0010. `docs/specs/risk-gate.md`, `execution.md` and `data-model.md` are updated alongside. `config/risk.yaml` is created under review. |
| VI. Paper Trading, US Equities | PASS | The universe is re-checked independently against daily reference data, failing closed when it's missing (G14). US common equity on XNYS, XNAS or XASE only. |
| VII. Assistant & Dashboard Read-Only | PASS | Both get `SELECT` only on the new tables. |
| Tech & Deployment | PASS | Python; Postgres the only durable store; no new service. The new dependencies are local libraries. |
| Development Workflow | PASS | Foundation-first (feature 2, after the data model). Tests cover every rule and the invariants. The loosened stop-loss (8% → 20%) was flagged and recorded in ADR 0010. |

**Post-design re-check (after Phase 1)**: all PASS; no Complexity Tracking entries.

## Project Structure

### Documentation (this feature)

```text
specs/002-risk-gate/
├── plan.md
├── research.md              # G1–G15
├── data-model.md            # migration 0006: new tables, role, risk_verdicts changes
├── quickstart.md
├── contracts/
│   ├── gate-interface.md    # service entry points, pure core, guarantees
│   ├── rejection-rules.md   # rule names and precedence
│   └── risk-config.md       # config/risk.yaml schema, loading rules, version
├── checklists/requirements.md
└── tasks.md                 # /speckit-tasks
```

Also amended: `specs/001-data-model/contracts/role-grants.md` (new role, new tables).

### Source Code (repository root)

```text
config/
└── risk.yaml                           # the limits (contracts/risk-config.md)

src/trading_agent/
├── storage/migrations/
│   └── 0006_risk_gate.sql              # stop_loss_triggers, instrument_reference,
│                                       # ta_reference_data, risk_verdicts changes, grants
└── risk/
    ├── __init__.py
    ├── model.py                        # frozen dataclasses: requests, context, order, verdict
    ├── config.py                       # RiskConfig, load_config(path), RiskConfigError, version
    ├── rules.py                        # rule-name constants (contracts/rejection-rules.md)
    ├── gate.py                         # pure core: evaluate(), choose_baseline()
    ├── calendar.py                     # XNYS: market_open(now), trading_day(now), open_time(day)
    └── service.py                      # evaluate_decision(), evaluate_stop_loss_trigger()

tests/
├── unit/risk/
│   ├── builders.py                     # concise constructors for contexts and requests
│   ├── test_config.py
│   ├── test_calendar.py
│   ├── test_buy_sizing.py              # US1
│   ├── test_hard_stops.py              # US2
│   ├── test_daily_loss.py              # US3 (pure parts) + choose_baseline
│   ├── test_stop_loss.py               # US4 (pure parts)
│   ├── test_precedence.py              # FR-016
│   ├── test_properties.py              # Hypothesis: SC-001..SC-003
│   └── test_config_import_guard.py     # only trading_agent.risk imports risk.config
└── integration/risk/
    ├── conftest.py                     # seeds: snapshots, reference rows, config file path
    ├── test_service_decisions.py
    ├── test_service_stop_loss.py
    ├── test_service_daily_loss.py
    └── test_order_cap_race.py
```

Also extended: `tests/integration/storage/grants_matrix.py` and `factories.py` (new objects and
role), and `tests/integration/storage/test_decision_chain.py` (a verdict needs exactly one source).

**Structure Decision**: a new `trading_agent.risk` package beside `storage`, split so the pure core
(`gate.py`, `model.py`, `rules.py`, `config.py`) has no import of `psycopg` or `service.py`. The
import-scan test enforces this too, since keeping I/O out of the core is what makes FR-002 true.
`calendar.py` stays out of the core as well: the service computes `market_open` and `trading_day`
and passes them in.

## Complexity Tracking

No constitution violations to justify.
