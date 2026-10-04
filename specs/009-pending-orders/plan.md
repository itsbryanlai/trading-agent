# Implementation Plan: The Risk Gate counts orders still in flight

**Branch**: `009-pending-buys` | **Date**: 2026-10-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-pending-orders/spec.md`

## Summary

The Risk Gate sizes each decision from the **settled holdings**: shares held, plus today's in-flight buys, minus today's in-flight sells. So re-deciding a target that's already being met orders nothing, and a changed target orders only the difference. A buy's cash reserve check also subtracts every in-flight buy's cost. The gate learns what's in flight from a new narrow view, `in_flight_orders` (migration 0013), the only new read it gets. Stop-loss exits and every hard stop are untouched.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: existing only (psycopg 3.2, exchange-calendars through `risk.calendar`). No new dependency.

**Storage**: the shared Postgres. Migration 0013 adds one view and its grants (data-model.md). No table changes.

**Testing**: pytest with Hypothesis for the pure core (research I9); integration tests against `ta-pg` for the view, the grants and an end-to-end evaluation. CI runs all three on the PR.

**Target Platform**: the gate's own loop process (ADR 0013, ADR 0019). Not deployed yet.

**Project Type**: a change to a deterministic service.

**Performance Goals**: one more small query per evaluation, over today's in-flight approvals (a handful).

**Constraints**: deterministic, no model or network call (Constitution I); no change to `config/risk.yaml`, Execution, the PM or the orchestrator; stop-loss exits unchanged.

**Scale/Scope**: a few in-flight approvals at any time; at most 5 exposure-increasing approvals a day.

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | The core stays a pure function: three more numbers in its context, no I/O. The new read happens in the existing caller, in the same locked transaction. | Pass |
| II. Analysts propose, PM decides | Not affected; the PM is unchanged. | Pass |
| III. Least privilege | One new read: `SELECT` on a narrow view exposing quantities and ceilings only, justified in the spec (FR-007). No grant on `orders` or `execution_refusals`, no write. An integration test proves it. | Pass |
| IV. Autonomous, one hard stop | No approval gate. The daily-loss breaker and stop-loss exits are untouched (research I6). | Pass |
| V. Spec and ADR first | Spec done; ADR 0020 proposed here, accepted before code. Gate contracts and `docs/specs/risk-gate.md` updated after it. `config/risk.yaml` untouched. | Pass, pending acceptance of ADR 0020 |
| VI. Paper only | Not affected. | Pass |
| VII. Read-only Assistant and dashboard | They gain `SELECT` on the new view, read-only, as for every table. | Pass |

**Re-check after design**: all pass.

## Project Structure

### Documentation (this feature)

```text
specs/009-pending-orders/
├── spec.md
├── plan.md                 # this file
├── research.md             # I1–I9
├── data-model.md           # the view and its grants
├── quickstart.md
├── contracts/gate-changes.md
├── checklists/requirements.md
└── tasks.md                # /speckit-tasks

docs/adr/0020-the-gate-counts-orders-in-flight.md   # proposed
```

### Source Code (repository root)

```text
src/trading_agent/
├── storage/migrations/0013_in_flight_orders.sql   # the view, its grants
└── risk/
    ├── model.py        # Context: in_flight_buy_qty, in_flight_sell_qty, in_flight_buy_cost
    ├── service.py      # _load_context reads the view
    └── gate.py         # _buy and _sell size from settled holdings (I4, I5)

tests/
├── unit/risk/          # scenarios, properties (I9); builders gain the three fields
└── integration/
    ├── storage/        # 0013 view per order state; grants matrix
    └── risk/           # same-target decisions end to end

specs/001-data-model/contracts/role-grants.md, specs/002-risk-gate/contracts/{gate-interface,rejection-rules}.md,
docs/specs/risk-gate.md, docs/specs/data-model.md, docs/adr/README.md
```

**Structure Decision**: changes stay inside `risk` and `storage`. `risk.gate` stays pure; only `risk.service` reads the database. The layering is unchanged.

## Things flagged for the owner

1. **Order logic** (CLAUDE.md): the gate's buy and sell sizing changes (research I4, I5). No limit changes, no rule is added, and with nothing in flight every verdict is as before (a property test proves it).
2. **A new read for the gate**: `SELECT` on one view (research I1). Not on `orders` or `execution_refusals`.
3. **A sell can't count shares still being bought** (research I5): with 50 held and 24 being bought, an exit sells 50, and the PM decides again once the buy fills. Canceling in-flight orders stays out of scope.
4. **ADR 0020 is `proposed`.** Your go-ahead on this plan accepts it; no code changes before that.

## Complexity Tracking

No constitution violations to justify.
