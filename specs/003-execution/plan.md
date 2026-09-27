# Implementation Plan: Execution

**Branch**: `003-execution` | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-execution/spec.md`

## Summary

The only component that holds the broker credential and places orders. Once a minute, a `tick`
syncs open orders and positions with the broker, refuses lapsed approvals, and processes today's
approved verdicts one at a time under an advisory lock. For each it first looks its identifier up
at the broker (so a crash can never cause a second order), then runs a fixed sequence of live
checks. A buy goes out as a day limit at the live ask, only under the ceiling, above the daily-loss
line, not paused, and inside the position and cash limits re-derived from live figures. An exit
goes out as a day market sell of shares actually held. Every approval ends as exactly one order or
one named refusal. The same tick runs the 30-minute stop-loss monitor, which routes triggers
through the Risk Gate, and records the pre-open snapshot. The core is pure; the broker sits behind
a port with one real adapter (paper address fixed in code) and one fake that every test uses.
Migration `0007` adopts the per-verdict order identifier ([ADR 0012](../../docs/adr/0012-order-identifier-per-verdict.md)),
adds `execution_refusals`, and grants `ta_execution` a read of the pause flag. Decisions:
[research.md](research.md).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: existing `psycopg[binary]` 3.2, `PyYAML` 6.0, `exchange-calendars` 4.13;
new: `alpaca-py` 0.44 (broker SDK, imported only by the adapter, E15). Dev: `pytest`, `hypothesis`,
`ruff` (existing).

**Storage**: PostgreSQL 16, extended by one forward-only migration (`0007_execution.sql`)

**Testing**: pytest. Offline unit and property tests of the pure core (Hypothesis ≥10,000 examples
for SC-004). Integration tests reuse the 001/002 harness with the fake broker. A suite-wide guard
fails any test that opens a non-local connection (E14).

**Target Platform**: Linux container on Railway (the worker service)

**Project Type**: Single Python project; adds the `trading_agent.execution` package

**Performance Goals**: None meaningful: a handful of orders a day, one tick a minute of a few
broker requests. Approvals reach the broker within about a minute of being recorded.

**Constraints**: Paper endpoint only, fixed in code (FR-013). No broker call in any test (FR-019).
Pure core with no I/O, clock, or environment reads (FR-016). Decimal arithmetic only. Broker SDK
imported by exactly one module.

**Scale/Scope**: 1 migration (1 new table, 1 altered table, grants); ~10 modules; 9 refusal reasons;
2 entry points (`startup`, `tick`) plus Execution's runner, and a small trigger runner for the gate.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this design satisfies it |
|---|---|---|
| I. Deterministic Trade Path (NON-NEGOTIABLE) | PASS | No model call. The pure core decides from plain values (E1). Only gate-approved verdicts are submitted, enforced by the composite FK on `orders` (001 R12). Stop-loss exits go through the gate, which evaluates triggers in its own process (E13). Execution re-derives the position and cash limits from live broker figures, refusing rather than trimming (E6). |
| II. Analysts Propose, PM Decides | PASS | Execution reads no reports or decisions, only verdicts. The config-loader import guard is widened to `execution` only; the PM is still forbidden (E12). |
| III. Least Privilege at the Database | PASS | `ta_execution` gains `S, I` on its own new table and a single-column read of `trading_paused`. Its `UPDATE` on `orders` is *narrowed* to the columns that change after submission (E10). The broker credential lives in one module of one component, and Execution's process holds no other component's credential: the gate evaluates triggers in its own process with its own login (E13, decided after `/speckit-analyze`). The grants matrix is amended and still tested for exact equality. |
| IV. Autonomous Operation, One Hard Stop | PASS | No human approval step. Once any snapshot since the open is at or below the daily-loss line, no buy is submitted for the rest of the day, even if equity recovers (E6 row 7); exits are never blocked. A broken risk config, which switches off the stop-loss monitor, is logged at error level every tick (E12). The pause blocks buys, never exits (FR-018). Nothing is liquidated by the breaker. |
| V. Spec-and-ADR-First | PASS | ADR 0012 (order identifier) is written before any code. The new table and grant are justified by the spec (FR-007, FR-018). `docs/specs/execution.md`, `docs/specs/data-model.md` and the role-grants contract are updated in this feature, referencing the ADR. `config/risk.yaml` is read, never written. |
| VI. Paper Trading, US Equities | PASS | Paper address fixed in code, any other configured address refused, and an authenticated read there required before start (E2). The deviation from the clarified wording (no documented "is paper" account field) is recorded in E2 and flagged for the owner. |
| VII. Assistant & Dashboard Read-Only | PASS | They gain `SELECT` only on `execution_refusals`. |
| Tech & Deployment | PASS | Python; Postgres the only durable store (outcomes, snapshots, triggers are rows; only the "which stop-loss window already ran" marker is in memory, and losing it just repeats a harmless check). No new Railway service decided here (E13). |
| Development Workflow | PASS | Foundation-first: Execution after the data model and the gate. Deterministic tests with fixed inputs and a fake broker. No risk limit is changed. Two new *price-usability* constants (60 s quote age, 30 min trade age, E9) are flagged here as order-logic parameters, not risk limits. |

**Post-design re-check (after Phase 1)**: all PASS; no Complexity Tracking entries.

## Project Structure

### Documentation (this feature)

```text
specs/003-execution/
├── plan.md
├── research.md                 # E1–E15
├── data-model.md               # migration 0007: orders changes, execution_refusals, grants
├── quickstart.md
├── contracts/
│   ├── execution-interface.md  # entry points, guarantees, what others rely on
│   ├── broker-port.md          # the broker Protocol, real adapter and fake
│   └── refusal-reasons.md      # refusal names, order, details
├── checklists/requirements.md
└── tasks.md                    # /speckit-tasks
```

Also added: `docs/adr/0012-order-identifier-per-verdict.md`. Amended during implementation:
`specs/001-data-model/contracts/role-grants.md`, `docs/specs/execution.md`,
`docs/specs/data-model.md`, `.env.example`.

### Source Code (repository root)

```text
src/trading_agent/
├── storage/migrations/
│   └── 0007_execution.sql              # orders id/status/columns, execution_refusals,
│                                       # exclusivity triggers, grants
└── execution/
    ├── __init__.py
    ├── __main__.py                     # runner: startup(), then tick() every 60 s
    ├── model.py                        # frozen values: approvals, live state, outcomes
    ├── ids.py                          # order_id(verdict)                        (pure)
    ├── checks.py                       # buy / exit check sequences (E6)         (pure)
    ├── fills.py                        # status mapping, fill deltas, positions  (pure)
    ├── monitor.py                      # stop-loss breaches from trades          (pure)
    ├── schedule.py                     # what's due at `now` (E13)               (pure but calendar)
    ├── reasons.py                      # refusal-reason constants (contract)
    ├── broker.py                       # Protocol, value types, exceptions
    ├── alpaca.py                       # the only broker SDK import; paper guard
    └── service.py                      # startup(), tick(): DB + broker + core

src/trading_agent/risk/
├── calendar.py                         # + close_time(day), early closes included
└── __main__.py                         # the gate's trigger runner (E13)

tests/
├── fakes/
│   └── broker.py                       # in-memory broker (contracts/broker-port.md)
├── conftest.py                         # suite-wide no-network guard (E14)
├── unit/execution/
│   ├── builders.py
│   ├── test_ids.py
│   ├── test_buy_checks.py              # US1
│   ├── test_exit_checks.py             # US2
│   ├── test_fills.py                   # US7
│   ├── test_monitor.py                 # US5 (pure parts)
│   ├── test_schedule.py                # US5, US6 timing
│   ├── test_paper_guard.py             # US4
│   ├── test_properties.py              # Hypothesis: SC-004
│   └── test_import_guard.py            # SDK and config-loader import rules
└── integration/execution/
    ├── conftest.py                     # fake broker + exec/gate connections
    ├── test_migration_0007.py          # id CHECKs, exclusivity, pause column grant
    ├── test_tick_buys.py
    ├── test_tick_exits.py
    ├── test_crash_recovery.py          # SC-002
    ├── test_fills_and_reconcile.py
    ├── test_stop_loss_monitor.py       # through the real gate
    └── test_pre_open_snapshot.py       # and Execution's baseline == the gate's
```

Also extended: `tests/integration/storage/grants_matrix.py` and `factories.py` (new table and
grants); `tests/unit/risk/test_config_import_guard.py` (allow `trading_agent.execution`).

**Structure Decision**: a new `trading_agent.execution` package beside `risk` and `storage`, with
the same pure-core / thin-service split as the gate. The core modules import neither `psycopg` nor
the broker SDK nor `broker.py`'s adapter, enforced by an import-scan test. `schedule.py` uses the
exchange calendar (like the gate's service does) but no clock: `now` is always passed in.

## Complexity Tracking

No constitution violations to justify.
