# Quickstart: Validating Execution

How to prove this feature works. Behavior: [spec.md](spec.md). Interfaces:
[contracts/](contracts/). Schema changes: [data-model.md](data-model.md).

**No step here contacts the broker, not even the paper account.** Every test runs against the fake
broker, and a suite-wide guard fails any test that opens a connection to anything but the local
Postgres (research E14). Running Execution against the real paper account is a separate action that
needs the owner's explicit go-ahead at the time (CLAUDE.md).

## Prerequisites

Same as features 001 and 002 ([002 quickstart](../002-risk-gate/quickstart.md)): Python 3.12, the
project installed with dev extras (`uv pip install --python .venv/bin/python -e ".[dev]"`, which
now also brings `alpaca-py`), and a disposable Postgres 16 for the integration suite. No broker key
is needed for any test.

## 1. Offline suite: the rules, with no database and no broker

```bash
.venv/bin/python -m pytest tests/ -q
```

**Expected**: all pass. For Execution this covers:

| Check | Spec |
|---|---|
| Order identifier: format, suffix from the verdict, stable across calls | FR-008 |
| Buy checks in order, with exact numbers: ask $201.50 under a $202 ceiling submits at $201.50; $202.01 refuses `quote_above_ceiling`; equity $80,000 on a $100,000 baseline refuses `daily_loss_line_crossed`; 41 × $200 on $100,000 refuses `max_position_pct` | US1, FR-003 to FR-005 |
| Open buy orders count against the ceiling and the reserve | Edge Cases |
| Exit checks: market sell of the approved qty; `shares_held_differ` when held − open sells < qty; never blocked by equity, pause, or config | US2, FR-006, FR-020 |
| Retry vs refuse: unreachable broker, zero or stale ask, market not open yet, and a bad config retry; every name in `contracts/refusal-reasons.md` is final | FR-007 |
| Fill application: 24 @ $200 + 6 @ $210 → 30 @ $202; a full sell deletes the position; cumulative-to-incremental fill arithmetic | US7, FR-011 |
| Status mapping: every broker status in research E7 maps as listed | FR-010 |
| Stop-loss scan: $160 on a $200 entry triggers, $161 doesn't; stale trade skipped; symbol with an open sell skipped | US5, FR-014 |
| Schedule: pre-open window on a trading day, nothing on a weekend or holiday, 30-minute windows clipped at an early close | FR-014, FR-015 |
| Paper guard: fixed address; a configured live or unknown address refuses; a failed account read refuses | US4, FR-013 |
| Hypothesis, ≥10,000 live account states: no submitted buy breaches the ceiling or reserve at its price, counting open orders | SC-004 |
| Import scan: only `execution/alpaca.py` imports the broker SDK; nothing outside `trading_agent.execution` imports it; the config loader is imported only by `risk` and `execution` | E2, E12 |

## 2. Integration suite: Execution against a real database and the fake broker

```bash
docker run --rm -d --name ta-pg -e POSTGRES_PASSWORD=dev -p 5433:5432 postgres:16
TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres \
  .venv/bin/python -m pytest tests/integration -m integration -q
```

**Expected**: all pass, including everything from features 001 and 002 (686 before this feature).
For Execution:

| Check | Spec |
|---|---|
| Migration 0007 applies on top of 0006; the grants matrix equals the database's own records both ways | FR-017 |
| The id format and verdict-suffix `CHECK`s reject a malformed or mismatched `orders.id` | FR-008 |
| An order and a refusal for the same verdict can't both exist; a refusal for a rejected verdict can't exist | E4 |
| `ta_execution` can read `trading_paused` and no other `system_state` column | FR-018 |
| A full tick: an approved buy becomes an order; a fill updates the order and the position; a stop-loss breach records a trigger, the gate approves, and a market sell goes out | US1, US5, US7 |
| Crash at every step of a submission, then tick again: one broker order per verdict, every time | SC-002 |
| Lapsed approvals from yesterday get `approval_expired`, and none reaches the broker | FR-002, SC-001 |
| Positions that disagree with the broker are overwritten with the broker's figures and logged | FR-011, SC-007 |
| Pre-open snapshot recorded once, before the open, and the gate then takes today's baseline from it; Execution's own baseline equals the gate's | US6, E11 |

## 3. Lint

```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

## 4. Mutation check before trusting new tests

As in feature 002: break a rule on purpose (e.g. compare the ask with `>=` instead of `>`, drop
open orders from the reserve check, skip the `find_order` lookup) and confirm at least one test
fails for each. Also confirm the property test actually generates submitted buys, not only
refusals.
