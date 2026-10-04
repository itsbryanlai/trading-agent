---

description: "Task list for the Risk Gate counting orders in flight (feature 009)"
---

# Tasks: The Risk Gate counts orders still in flight

**Input**: Design documents from `/specs/009-pending-orders/`

**Prerequisites**: plan.md, spec.md (with Clarifications), research.md (I1–I9), data-model.md, contracts/gate-changes.md, quickstart.md; [ADR 0020](../../docs/adr/0020-the-gate-counts-orders-in-flight.md) (accepted 2026-10-05), with ADRs 0005, 0006, 0013 and 0019.

**Tests**: included and written first. Every safety claim (SC-001 to SC-004) is a Hypothesis property of the pure core, plus the spec's scenarios with exact quantities.

**Organization**: Foundational adds the view and the context fields. US1 (same target), US2 (changed target) and US3 (exits and hard stops untouched) follow, then polish.

## Format: `[ID] [P?] [Story] Description`

## Conventions every task follows

- **⚠️ Order logic.** Every change to `risk/gate.py` is order logic, covered by ADR 0020. Change only what the task names. No change to `config/risk.yaml`, to any rule's name or precedence, to `_stop_loss`, to the account or universe checks, to Execution, the PM or the orchestrator.
- **Atomic commits** (CLAUDE.md): one commit per task, or per group the task names, as it finishes, in the style `Risk Gate (feature 009): what (T0NN)` or `Storage (feature 009): …` or `Docs (feature 009): …`. Stage by explicit path; never `git add -A`; never stage `.venv` or `job_*.csv`. Tick the task's checkbox in its commit.
- **Modular code**: the layering in `pyproject.toml`; no module over 600 lines; no `# noqa`.
- **After each logic change**: the relevant tests, `PYTHONPATH=src scripts/lint.sh`, `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests`. Run with `PYTHONPATH=src` (the worktree's `.venv` is a symlink whose editable install points at the main checkout). Check exit codes unpiped.
- **Pure core**: `risk/gate.py` does no I/O and reads no clock; the new numbers arrive in `Context`.
- **Integration tests** use the rolled-back `conn` fixture and `as_role`, on the next real session from `risk.calendar`, never a fixed date the database's `now()` will pass.
- **Mutation-check every new test**: break the code, see a test fail, restore the file's saved text by editing. Never `git checkout` or `git stash`. Use Python `subprocess` timeouts.
- **No credentials, no network.**

---

## Phase 1: Foundational (blocks every story)

- [x] T001 Write the failing test `tests/integration/storage/test_in_flight_orders.py` (data-model.md, research I1). As the migration admin, build verdicts with the existing helpers (`tests/integration/storage/chain.py`) and assert the view `in_flight_orders`:
  - shows an approved buy with no order and no refusal, with `unsettled_qty` = its approved qty and `limit_price` = its ceiling;
  - shows a `submitted` order with its full qty, and a `partially_filled` one with qty − `fill_qty`;
  - shows a sell with `limit_price` null;
  - leaves out `filled`, `rejected`, `canceled` and `expired` orders, an approval Execution refused, a rejected verdict, and a partially filled order whose remainder is 0;
  - carries the verdict's `trading_day`;
  - exposes exactly the five columns `trading_day, symbol, side, unsettled_qty, limit_price`.
  Also: `ta_risk_gate`, `ta_assistant` and `ta_dashboard` can select from it; `ta_risk_gate` still can't select `orders` or `execution_refusals` (`42501`); no role gained a write. Update `tests/integration/storage/grants_matrix.py` for the three new `SELECT`s.
- [x] T002 Add `src/trading_agent/storage/migrations/0013_in_flight_orders.sql` exactly as data-model.md says: a header citing feature 009 and ADR 0020; `CREATE VIEW in_flight_orders AS …` with no `security_invoker` (owner's rights, like `reference_candidate_symbols`); the three grants. T001 passes; the full integration suite passes. One commit with T001 and the grants-matrix change, `Storage (feature 009): migration 0013 adds the in_flight_orders view (T001, T002, ADR 0020)`.
- [x] T003 Add the four fields of research I3 (`in_flight_buy_qty`, `in_flight_buy_cost_symbol`, `in_flight_sell_qty`, `in_flight_buy_cost_all`; all `Decimal`, default `Decimal(0)`) to `risk.model.Context`, and give `tests/unit/risk/builders.py` the four at 0. In `risk/service.py`'s `_load_context`, read today's rows of `in_flight_orders` in the same cursor and transaction. Nothing in `gate.py` reads them yet, so every existing test passes unchanged. Commit with an integration assertion in `tests/integration/risk/` that `_load_context` returns the right four numbers for a symbol with one buy and one sell in flight and another symbol with a buy in flight, and 0s for an earlier day's approval.

**Checkpoint**: the gate sees what's in flight; no verdict has changed yet.

---

## Phase 2: User Story 1 - Re-deciding the same target orders nothing more (P1) 🎯 MVP

**Independent Test**: spec US1's scenarios.

- [x] T004 [P] [US1] `tests/unit/risk/test_in_flight_same_target.py`: spec US1-1 (an in-flight buy of 24 at a $200 quote, equity $100,000, target 5%: `target_already_met`), US1-2 (10 of 24 filled, so 10 held and 14 in flight: `target_already_met`), US1-3 (50 held at $200, an in-flight sell to 2% of 40, target 2% again: `target_already_met`). Plus a Hypothesis property (SC-001): for random equity, cash, quote, tolerance, held and target where the gate approves a decision, adding that approval as in flight (its quantity, and for a buy its cost at its ceiling) and evaluating the same decision again is **not approved**, for buys and sells (analyze F4). Include research I4's low-price example: equity $100,000, quote $10, 1% tolerance, target 5%: 495, then not approved (analyze F2).
- [x] T005 [US1] In `risk/gate.py`, size buys and sells exactly as research I4 and I5 say: the settled value with in-flight buys at their ceiling, the position room counting held plus in-flight buys and never subtracting sells, the cash reserve's in-flight cost, and the sell's `available` cap. Don't change `_stop_loss`, the account or universe checks, or rule precedence. T004 passes and every existing risk test passes unchanged. One commit with T004, `Risk Gate (feature 009): size decisions from settled holdings (T004, T005, ADR 0020)`.

---

## Phase 3: User Story 2 - A changed target orders only the difference (P1)

- [ ] T006 [P] [US2] `tests/unit/risk/test_in_flight_changed_target.py`: spec US2-1 ($50,000 cash, an in-flight buy of 24 at a $202 ceiling, target 7% at $200 with a 1% tolerance: a buy of exactly 10), US2-2 (nothing held, an in-flight buy, a sell to 0: `no_position`), US2-3 (50 held, an in-flight sell of 30, target 0: a sell of 20), and research I5's example (50 held, 24 being bought, target 0: a sell of 50). Plus a Hypothesis property (SC-002): an approved sell's quantity never exceeds `shares_held − in_flight_sell_qty`. Plus (SC-002a, FR-005): an approved buy's `qty × limit_price + in_flight_buy_cost_all` leaves cash at or above `cash_reserve_pct` of equity, and `shares_held + in_flight_buy_qty + qty` stays within `max_position_pct` at the ceiling, whatever `in_flight_sell_qty` is. If T005 already makes these pass, commit the tests alone; otherwise fix `gate.py` in the same commit.

---

## Phase 4: User Story 3 - Exits and the hard stops are untouched (P1)

- [ ] T007 [P] [US3] `tests/unit/risk/test_in_flight_unchanged.py`: spec US3-1 to US3-4, with US3-4's exact numbers ($100,000 equity, $25,000 cash, 20% reserve, $4,000 of in-flight MSFT buys: an AAPL buy is sized as if cash were $21,000). Plus two Hypothesis properties: with the four in-flight values at 0, every verdict and `record_halt` equal today's for the same inputs (SC-003: build the context twice, once with explicit zeros and once with the builder's defaults, and compare against a copy of the pre-feature `_buy` and `_sell` kept in the test file as the oracle); and any stop-loss trigger's verdict is identical for arbitrary in-flight values (SC-004). Plus two cases: analyze F1's (50 held at $200, a sell of 40 in flight, a buy to 5%: rejected `max_position_pct`, never approved; today's gate, blind to the sell, says `direction_contradicts_target`); and research I5a's (an in-flight sell larger than `shares_held`: a sell is `target_already_met`, and a buy's position room is no looser than today's). Commit.
- [ ] T008 [US3] `tests/integration/risk/test_in_flight_end_to_end.py` on the next real session: a buy decision approved, its order `submitted` (no fill), then a second decision with the same target through `evaluate_decision` is `target_already_met`; after the order is marked `filled` and the position written, a third decision with a higher target buys only the difference. Commit.

---

## Phase 5: Polish

- [ ] T009 [P] Docs, one commit (`Docs (feature 009): …`):
  - `specs/002-risk-gate/contracts/gate-interface.md` and `rejection-rules.md` per contracts/gate-changes.md;
  - `specs/001-data-model/contracts/role-grants.md`: the three `SELECT`s on `in_flight_orders`;
  - `docs/specs/risk-gate.md`: sizing from settled holdings, the cash reserve counting in-flight buys, ADR 0020;
  - `docs/specs/data-model.md`: a section for the `in_flight_orders` view.
- [ ] T010 Run quickstart steps 1–3, each unpiped. Fix anything in its own commit.
- [ ] T011 `/speckit-converge` (Sonnet subagent) and an adversarial review (subagent on the session's model), in parallel; act on findings in their own commits.

---

## Dependencies & Execution Order

- T001 → T002 → T003 → T004/T005 → T006, T007, T008 (parallel) → T009 → T010 → T011.
- Every task touching `risk/gate.py` (T005, and T006 only if it must fix the core) is reviewed line by line by the main session before the next phase.

## Implementation Strategy

1. Foundational: the view and the context, with no verdict changing.
2. US1: the fix itself.
3. US2 and US3: proof that changed targets, exits and hard stops behave.
4. Polish: docs, the full run, converge and the adversarial review.
