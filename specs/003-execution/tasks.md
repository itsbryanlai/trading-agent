---

description: "Task list for Execution (feature 003)"
---

# Tasks: Execution

**Input**: Design documents from `/specs/003-execution/`

**Prerequisites**: plan.md, spec.md, research.md (E1–E15), data-model.md,
contracts/execution-interface.md, contracts/broker-port.md, contracts/refusal-reasons.md,
quickstart.md, [ADR 0012](../../docs/adr/0012-order-identifier-per-verdict.md). The shared grants
contract is `specs/001-data-model/contracts/role-grants.md`, amended for this feature.

**Tests**: Included, and written first within each story. The spec requires every behaviour to be
tested against a fake broker (FR-019), and the constitution requires fixed-input tests for
Execution. SC-002 and SC-004 are universal claims, backed by crash-injection and Hypothesis tests.

**Organization**: One phase per user story (US1–US7 in spec.md). The pure core grows by story;
`service.py` gains `tick()` in US1 and one duty per later story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US7

## Conventions every task follows

- **No task ever contacts the broker**, including the paper account. Tests use
  `tests/fakes/broker.py`; the suite-wide guard (T008) fails any non-local connection. Running
  against the paper account is a separate action that needs the owner's explicit go-ahead
  (CLAUDE.md).
- **Money, prices and quantities are `decimal.Decimal`**, never `float` (E6). Broker strings convert
  at the adapter boundary.
- **The pure core** (`execution/model.py`, `ids.py`, `checks.py`, `fills.py`, `monitor.py`,
  `reasons.py`) never imports `psycopg`, `alpaca`, `os`, `execution/service.py` or
  `execution/alpaca.py`, and never reads the clock. `schedule.py` may import
  `trading_agent.risk.calendar` but never reads the clock. `now` is always an argument.
- **Refusal reasons** are referenced only through the constants in `execution/reasons.py`, whose
  strings match `contracts/refusal-reasons.md` exactly.
- **Service functions take a `psycopg.Connection`** connected as (or `SET ROLE` to) `ta_execution`,
  a `Broker`, `now: datetime`, and `config_path: Path`. Each approval is processed in its own
  `conn.transaction()` holding `pg_advisory_xact_lock(0x65786563)` ("exec"), distinct from the
  gate's key (E5).
- **Test clock**: Monday **2026-09-28 14:00 UTC (10:00 ET)**, market open 13:30 UTC, close 20:00
  UTC, unless the test is about another time. Pre-open: 12:00 UTC. Weekend: 2026-09-26. Holiday:
  2026-11-26. Early close: 2026-11-27 (13:00 ET = 18:00 UTC). Same as feature 002.
- **Test connections**: integration tests use feature 001's rolled-back `conn` fixture (outer
  transaction opened first, so `conn.transaction()` is a savepoint) and `as_role`.
- A committed migration is never edited; `0007` is new.

---

## Phase 1: Setup

- [X] T001 Add runtime dependency `alpaca-py>=0.44,<0.45` to `pyproject.toml` (E15); reinstall with `uv pip install --python .venv/bin/python -e ".[dev]"` and confirm `import alpaca.trading.client, alpaca.data.historical` works. Do not construct any client
- [X] T002 [P] Create packages: `src/trading_agent/execution/__init__.py`, `tests/unit/execution/__init__.py`, `tests/integration/execution/__init__.py`, `tests/fakes/__init__.py`
- [X] T003 [P] Add to `.env.example`, names and comments only, no values (E15): `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` (paper account keys, Execution only, never given to any other component); `ALPACA_BASE_URL` (optional; if set it must equal `https://paper-api.alpaca.markets` or Execution refuses to start); `EXECUTION_DATABASE_URL` (a login in `ta_execution`); `RISK_GATE_DATABASE_URL` (a login in `ta_risk_gate`, used only by the gate's trigger runner `python -m trading_agent.risk`; never given to Execution's process, research E13)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Vocabulary (reasons, value types), the broker port and its fake, the order
identifier, migration `0007`, and the guards. Every story depends on these.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 [P] Write `src/trading_agent/execution/reasons.py`: one constant per name in `contracts/refusal-reasons.md` (`APPROVAL_EXPIRED = "approval_expired"`, `IDENTIFIER_CLASH`, `TRADING_PAUSED`, `NO_DAILY_BASELINE`, `DAILY_LOSS_LINE_CROSSED`, `QUOTE_ABOVE_CEILING`, `MAX_POSITION_PCT`, `CASH_RESERVE_PCT`, `SHARES_HELD_DIFFER`) and `ALL = frozenset({...})`; plus `tests/unit/execution/test_reasons_contract.py` asserting `ALL` equals the set of backticked names in the contract's tables (parse the markdown)
- [X] T005 [P] Write `src/trading_agent/execution/broker.py` exactly per `contracts/broker-port.md`: frozen dataclasses `Account(account_number, equity, cash, buying_power)`, `BrokerPosition(symbol, qty, avg_entry_price)`, `Quote(symbol, ask, timestamp)`, `Trade(symbol, price, timestamp)`, `OrderRequest(client_order_id, symbol, side: Literal["buy","sell"], qty: int, order_type: Literal["limit","market"], limit_price: Decimal | None, time_in_force: Literal["day"] = "day")` (validates `limit_price` is set iff `order_type == "limit"`), `BrokerOrder(broker_order_id, client_order_id, symbol, side, qty, order_type, limit_price: Decimal | None, status: str, filled_qty, filled_avg_price, submitted_at, reason)`; exceptions `BrokerUnavailable`, `OrderRejected(reason)`, `NotPaperTrading`; and `class Broker(Protocol)` with `get_account`, `get_positions`, `get_latest_ask`, `get_latest_trade`, `find_order`, `get_order`, `submit_order`. No cancel/replace/close call
- [X] T006 [P] Write `src/trading_agent/execution/model.py`, frozen dataclasses: `Approval(verdict_id: UUID, order: ApprovedOrder)` (reusing `trading_agent.risk.model.ApprovedOrder`); `BuyLive(equity, cash, held_qty, open_buy_qty_symbol, open_buy_cost_all, ask: Quote | None, baseline: Decimal | None, min_equity_since_open: Decimal | None, paused: bool, config: RiskConfig | None)` (`min_equity_since_open` is the lowest equity among today's snapshots taken at or after the open, including the one just recorded; research E6 row 7); `ExitLive(held_qty, open_sell_qty_symbol)`; outcomes `Submit(request: OrderRequest)`, `Refuse(reason: str, details: dict)`, `Retry(why: str)`; `TickReport` counters (`submitted, refused, retried, fills_applied, reconciled, triggers, snapshot_taken`)
- [X] T007 [P] Write `tests/fakes/broker.py`, an in-memory `FakeBroker` satisfying the `Broker` protocol and everything under "What the fake must model" in `contracts/broker-port.md`: settable `account`, `positions`, per-symbol `quotes` and `trades`; `submissions` list; `find_order`/`get_order` from its own book; `fill(client_order_id, qty, price)` updating the order's cumulative `filled_qty`/`filled_avg_price`, positions (weighted average on buys) and cash; `close_session()` moving open day orders to `canceled` (unfilled) or `done_for_day` (partly filled); `reject_next(reason)`; `reject_duplicate_client_ids=True` option making `submit_order` raise `OrderRejected` for a client id it already holds (research E5's double-check); `fail(method_name, after_effect=False)` raising `BrokerUnavailable` on the next call to that method, with `after_effect=True` on `submit_order` recording the order first (the "maybe placed" timeout). Plus `tests/unit/execution/test_fake_broker.py` covering each behaviour, so later tests can trust it
- [X] T008 [P] Write the suite-wide no-network guard in `tests/conftest.py`: an autouse fixture that wraps `socket.socket.connect` (and `create_connection`) to raise unless the host is `localhost`, `127.0.0.1` or `::1` (the local Postgres); plus `tests/unit/execution/test_network_guard.py` proving a connect to `paper-api.alpaca.markets:443` raises inside a test (E14, SC-008). Confirm the existing 001/002 suites still pass with it
- [X] T009 [P] Write tests first in `tests/unit/execution/test_ids.py`, then implement `order_id(verdict_id: UUID, order: ApprovedOrder) -> str` in `src/trading_agent/execution/ids.py`: `2026-09-28-AAPL-buy-3f9c2a1b` for verdict `3f9c2a1b-…`; always matches `^\d{4}-\d{2}-\d{2}-[A-Z][A-Z0-9.]*-(buy|sell)-[0-9a-f]{8}$`; `BRK.B` is kept; same inputs give the same id; two verdicts differing only after the 8th hex char give the same id (the clash case FR-008 must detect); length ≤ 128
- [X] T010 [P] Write `tests/unit/execution/builders.py`: `approved_buy(qty=24, ceiling="202", symbol="AAPL", day=2026-09-28, verdict_id=...)`, `approved_sell(qty=50, source="decision"|"stop_loss")`, `buy_live(**overrides)` defaulting to a passing state (equity 100,000, cash 100,000, held 0, no open orders, ask 201.50 at the test clock, baseline 100,000, not paused, the repo config), `exit_live(**overrides)`, and `repo_config()` loaded via `trading_agent.risk.config.load_config`
- [X] T011 Extend storage tests for migration `0007`, written before it, exactly per `data-model.md`: in `tests/integration/storage/grants_matrix.py` change `orders` for `ta_execution` to `{"S", "I", "U:broker_order_id", "U:status", "U:fill_qty", "U:fill_price", "U:broker_reason", "U:updated_at"}`, add `execution_refusals` (`ta_execution` {S, I}; `ta_journal`, `ta_assistant`, `ta_dashboard` {S}), and `"S:trading_paused"` for `ta_execution` on `system_state`. The catalog side of `test_grants.py` already reads column ACLs from `pg_attribute` and yields `S:<col>`; **do not** switch it to `information_schema` (its docstring explains why). Extend only the probe side in `factories.py`: add `columns_for_select: tuple[str, ...] = ()` to `ObjectSpec`, make `ops_for` add `S:<col>` for each, and probe it with `SELECT <col> FROM <table> WHERE false`; change the bare `S` probe from `SELECT 1 …` to `SELECT * FROM <table> WHERE false`, so a column-only grant reads as "allowed" for `S:<col>` and "denied" for bare `S` (verify the existing 001/002 cases are unaffected); in `test_grants.py::test_privilege_matches_contract`, count a table-level grant as covering its columns (`granted = GRANTS[name].get(role, set())`; `expected = "allowed" if op in granted or (op[:2] in ("S:", "U:") and op[0] in granted) else "denied"`), leaving the catalog-equality test unchanged, because five roles hold table-level `S` on `system_state` and their `S:<col>` probes succeed (analyze G2); set `columns_for_select` on `system_state` to all six columns and `columns_for_update` on `orders` to every `orders` column so the `U:<col>` ops are probeable; add an `ObjectSpec` for `execution_refusals`; in `chain.py` make `insert_order` build a valid ADR 0012 id from the verdict and add `insert_refusal(conn, verdict_id, reason)`. Add `tests/integration/storage/test_execution_outcomes.py`: an id not matching the format fails `23514`; an id whose suffix isn't `left(risk_verdict_id::text, 8)` fails `23514`; `status = 'expired'` is accepted and `'pending'` fails `23514`; a buy order row with `limit_price` NULL, or a sell row with one, fails `23514`; a refusal with a reason outside the contract fails `23514`; a refusal for a rejected verdict fails `23503`; an order then a refusal for the same verdict fails, and a refusal then an order fails (the exclusivity triggers, SQLSTATE `23000`); a second refusal for a verdict fails `23505`; `details` must be a JSON object; as `ta_execution`, `SELECT trading_paused FROM system_state` works and `SELECT halt_triggered_on FROM system_state` fails `42501`; as `ta_execution`, `UPDATE orders SET risk_verdict_id = …` fails `42501`
- [X] T012 Write `src/trading_agent/storage/migrations/0007_execution.sql` per `data-model.md` to make T011 pass: on `orders`: `ADD CONSTRAINT orders_id_format CHECK (id ~ '^\d{4}-\d{2}-\d{2}-[A-Z][A-Z0-9.]*-(buy|sell)-[0-9a-f]{8}$')`, `ADD CONSTRAINT orders_id_names_verdict CHECK (right(id, 8) = left(risk_verdict_id::text, 8))`, replace the status CHECK with `status IN ('submitted', 'partially_filled', 'filled', 'rejected', 'canceled', 'expired')`, `ADD COLUMN limit_price numeric(14,4) CHECK (limit_price > 0)`, `ADD CONSTRAINT orders_buy_has_limit CHECK ((id ~ '-buy-[0-9a-f]{8}$') = (limit_price IS NOT NULL))`, `ADD COLUMN broker_reason text` (nullable), and a `COMMENT ON COLUMN orders.id` citing ADR 0012; create `execution_refusals (id uuid PK DEFAULT gen_random_uuid(), risk_verdict_id uuid NOT NULL UNIQUE, verdict text NOT NULL DEFAULT 'approved' CHECK (verdict = 'approved'), reason text NOT NULL CHECK (reason IN (<the nine names in contracts/refusal-reasons.md>)), details jsonb NOT NULL CHECK (jsonb_typeof(details) = 'object'), refused_at timestamptz NOT NULL, FOREIGN KEY (risk_verdict_id, verdict) REFERENCES risk_verdicts (id, verdict))`; a `BEFORE INSERT` trigger function `execution_outcome_exclusive()` on both tables raising `USING ERRCODE = 'integrity_constraint_violation'` (`23000`) if the other table has a row for `NEW.risk_verdict_id`; grants: `REVOKE UPDATE ON orders FROM ta_execution` then `GRANT UPDATE (broker_order_id, status, fill_qty, fill_price, broker_reason, updated_at) ON orders TO ta_execution`, `GRANT SELECT, INSERT ON execution_refusals TO ta_execution`, `GRANT SELECT ON execution_refusals TO ta_journal, ta_assistant, ta_dashboard`, `GRANT SELECT (trading_paused) ON system_state TO ta_execution`
- [X] T013 Amend `specs/001-data-model/contracts/role-grants.md` to match T011/T012 exactly (the `orders` row, a new `execution_refusals` row, the `system_state` column read, and the forbidden-operation example "second order for the same verdict" per ADR 0012), with a note "amended by `specs/003-execution`"
- [X] T014 [P] Add `close_time(day) -> datetime` (UTC, tz-aware; raises if `day` isn't a session) to `src/trading_agent/risk/calendar.py`, tests first in `tests/unit/risk/test_calendar.py`: 2026-09-28 closes 20:00 UTC; 2026-11-27 (early close) 18:00 UTC; 2026-09-26 raises. Used for "the market has closed" (research E6 row 1), the lapsed sweep, and the monitor's last window (analyze U1)
- [X] T015 [P] Write `tests/unit/execution/test_import_guard.py` and widen `tests/unit/risk/test_config_import_guard.py`: `trading_agent.risk.config` may be imported only inside `trading_agent/risk/` and `trading_agent/execution/` (E12; the PM and everything else still forbidden); only `trading_agent/execution/alpaca.py` imports anything under `alpaca`; nothing outside `trading_agent/execution/` imports `trading_agent.execution.alpaca`; the pure-core modules listed in Conventions import none of `psycopg`, `alpaca`, `os`, `trading_agent.execution.service`, `trading_agent.execution.alpaca`, and call no `.now()`/`.today()`/`.utcnow()`; `schedule.py` also calls no clock; a vacuity check that the core modules exist
- [X] T016 Run `.venv/bin/python -m pytest tests/ -q` and the integration suite with `TEST_DATABASE_URL`; confirm T004–T015 pass with all of 001's and 002's tests (686 before this feature)

**Checkpoint**: vocabulary, broker port and fake, identifier, and schema are in place; the grants
catalog test still proves database = contract.

---

## Phase 3: User Story 1 - An approved buy is placed only if live numbers still confirm it (Priority: P1) 🎯 MVP

**Goal**: An approved buy from today becomes a day limit order at the live ask, or a named final
refusal, or a retry, following research E6's table exactly.

**Independent Test**: Unit-check each row of the E6 buy table with the spec's numbers; then run
`tick()` against the test database and the fake broker and see one order row (or one refusal row)
and one pre-buy account snapshot.

### Tests for User Story 1 ⚠️ write first, confirm they fail

- [X] T017 [P] [US1] Write `tests/unit/execution/test_buy_checks.py` for `check_buy(approval, live, today, now, market_open, id_clash) -> Submit | Refuse | Retry`, one test per E6 buy row and each spec US1 scenario: (1) ask 201.50 ≤ ceiling 202 → `Submit` of a day limit buy of 24 AAPL at 201.50 with `client_order_id` = `order_id(...)`; (2) ask 202.01 → `Refuse(QUOTE_ABOVE_CEILING)` with `ask`, `ceiling`, `quote_time` in details; ask exactly 202.00 → submits; (3) equity 80,000 on baseline 100,000 (line 80,000) → `DAILY_LOSS_LINE_CROSSED`; 80,000.01 → passes; live equity 95,000 but `min_equity_since_open` 79,000 (crossed earlier, since recovered) → `DAILY_LOSS_LINE_CROSSED` (Principle IV, analyze C1); (4) 10 held, qty 31, ask 200, equity 100,000 → `MAX_POSITION_PCT` (8,200 > 8,000); qty 30 → submits (8,000 = 8,000); (5) cash 25,000, equity 100,000, ask 200: qty 26 → `CASH_RESERVE_PCT` (25,000 − 5,200 = 19,800 < 20,000); qty 25 → submits (exactly 20,000 left); (6) approval day 2026-09-25 → `APPROVAL_EXPIRED`; today after 20:00 UTC → `APPROVAL_EXPIRED`; today at 13:00 UTC (before open) → `Retry`; (7) `paused=True` → `TRADING_PAUSED`; (8) `baseline=None` → `NO_DAILY_BASELINE`; `config=None` → `Retry`; (9) ask `None`, ask 0, ask older than 60 s → `Retry`; (10) `id_clash=True` → `IDENTIFIER_CLASH`; (11) open buy qty and open buy cost count: 20 held + 15 open + qty 6 at 200 → `MAX_POSITION_PCT`; open buy cost 75,000 (other symbols) with cash 100,000, held 0, ask 200: qty 30 → `CASH_RESERVE_PCT` (100,000 − 75,000 − 6,000 = 19,000 < 20,000; position 6,000 is under the 8,000 ceiling, so row 10 doesn't fire first), qty 25 → submits (exactly 20,000 left); (12) precedence: a state failing paused, baseline and ceiling at once names `TRADING_PAUSED` (E6 order); (13) an ask of 201.505 is submitted at 201.50 (rounded down); every submitted `limit_price` ≤ the ceiling; (14) details values are strings, never floats
- [X] T018 [P] [US1] Write `tests/integration/execution/conftest.py` and `tests/integration/execution/test_tick_buys.py`. Conftest (as admin in the rolled-back `conn`): reuse `tests/integration/risk/conftest.py` seeders (`insert_snapshot` pre-open at 12:00 UTC, `insert_reference`); `approved_verdict(conn, order: ApprovedOrder) -> UUID` inserting a decision (or trigger) and an approved `risk_verdicts` row with that `approved_order` JSON, `trading_day` and `config_version`; a `fake_broker` fixture; `exec_tick(conn, broker, now)` running `tick(..., _allow_savepoints=True)` inside `as_role(conn, "ta_execution")` (the gate-runner helper is added in T042, once the runner exists). Tests: an approved buy with a passing fake broker → exactly one `orders` row with the ADR 0012 id, `status` from the broker, `limit_price` = the ask, `risk_verdict_id` set, and the fake has exactly one submission with that client id; one `account_snapshots` row with the fake's equity/cash/buying power, recorded before the submission; with an admin-seeded snapshot at 13:50 UTC of 79,000 on a 100,000 baseline and the fake's equity back at 95,000 → `daily_loss_line_crossed` refusal; ask above the ceiling → one `execution_refusals` row `quote_above_ceiling`, no order, no submission; `system_state.trading_paused = true` → `trading_paused` refusal, and an approved sell in the same tick is still submitted; a second tick changes nothing; a rejected verdict is never touched; a transient failure (`fake.fail("get_latest_ask")`) → no row, and the next tick submits

### Implementation for User Story 1

- [X] T019 [US1] Implement `check_buy` in `src/trading_agent/execution/checks.py` to make T017 pass: the E6 buy table in order, `Decimal` throughout, constants `MAX_QUOTE_AGE = timedelta(seconds=60)`, limit price = ask rounded down to the cent (`quantize(Decimal("0.01"), ROUND_DOWN)`), line = `baseline × (1 − daily_loss_halt_pct/100)`, position test `(held + open_buy_qty_symbol + qty) × ask > max_position_pct/100 × equity`, reserve test `cash − open_buy_cost_all − qty × ask < cash_reserve_pct/100 × equity`, details as decimal strings and ISO times
- [X] T020 [US1] Implement the approval path in `src/trading_agent/execution/service.py`: `tick(now, broker, conn, config_path=DEFAULT_CONFIG_PATH) -> TickReport` which (for now) loads today's approved verdicts with no order and no refusal (`risk_verdicts` where `verdict = 'approved'` and `trading_day = today`), **exits first, then buys**, oldest first within each, and, for each, in its own `conn.transaction()` under the "exec" advisory lock and its own `try` block (an unexpected error is logged with the verdict id and the tick continues, research E5): re-check no outcome exists; if an `orders` row with this id exists for another verdict, refuse `identifier_clash` and stop (**before** any broker lookup, E5 step 2); `broker.find_order(order_id)` → if found, insert the `orders` row from it, including its `limit_price`, and stop (E5 step 3); for a buy, read `trading_paused` from `system_state`, today's baseline (E11: equity of the last `account_snapshots` row on today's New York date with `taken_at < calendar.open_time(today)`), the lowest equity among snapshots with `taken_at >= calendar.open_time(today)` (including the one recorded next), load config (a `RiskConfigError` → `config=None`), fetch `get_account()` and insert an `account_snapshots` row with its exact values, fetch positions, the ask, and sum open buy orders by joining `orders` to `risk_verdicts.approved_order` (non-final status, `approved_order->>'side' = 'buy'`, remaining = `(approved_order->>'qty')::int − coalesce(fill_qty, 0)`, cost = remaining × `orders.limit_price`; `orders` has no symbol/side/qty columns); call `check_buy`; on `Refuse` insert an `execution_refusals` row; on `Submit` call `submit_order` and insert the `orders` row (`submitted_at` from the broker, `status` mapped per E7, `limit_price`); on `OrderRejected` call `find_order` once more and, if an order exists under the id, record that instead; only otherwise insert an `orders` row with `status = 'rejected'`, `broker_order_id` NULL, `broker_reason`, and (for a buy) the submitted `limit_price` (research E5); on `BrokerUnavailable` or `Retry` roll back that approval's transaction, log, and count a retry; if the `BrokerUnavailable` came from `submit_order`, add the verdict to the tick-state's in-memory "maybe placed" set with `now`, and skip resubmitting it until a `find_order` at least 2 minutes later still returns `None` (research E5, analyze S7). `tick` raises unless `conn.autocommit` or `_allow_savepoints=True` (analyze S8). A `psycopg.OperationalError` is never caught by the per-unit `try` blocks; it propagates out of `tick` (analyze S9). Market open and trading day from `trading_agent.risk.calendar`
- [X] T021 [US1] Run the US1 tests; confirm T017 and T018 pass

**Checkpoint**: MVP. Approved buys reach the fake broker only when live numbers confirm them.

---

## Phase 4: User Story 2 - Approved sells and stop-loss exits are placed as market orders (Priority: P1)

**Goal**: Exits go out as day market sells of shares actually held, never blocked by equity, pause
or config.

**Independent Test**: Unit-check the E6 exit table; through `tick()`, see a market sell submitted
with equity below the line, the pause on, and a broken config path.

### Tests for User Story 2 ⚠️ write first, confirm they fail

- [X] T022 [P] [US2] Write `tests/unit/execution/test_exit_checks.py` for `check_exit(approval, live, today, now, market_open, id_clash)`: 50 held, sell 50 → `Submit` of a day market sell of 50, `limit_price` None; 30 held, sell 50 → `Refuse(SHARES_HELD_DIFFER)` with `held`, `open_sell_qty`, `qty`; 50 held with 50 already in an open sell, stop-loss exit of 50 → `SHARES_HELD_DIFFER`; yesterday's approval → `APPROVAL_EXPIRED`; before today's open → `Retry`; `id_clash` → `IDENTIFIER_CLASH`; the function takes no equity, baseline, pause or config input at all (assert its signature), so none can block an exit
- [X] T023 [P] [US2] Write `tests/integration/execution/test_tick_exits.py`: an approved decision sell and an approved stop-loss exit of the same symbol on the same day get two different order ids and are both submitted when enough shares are held (ADR 0012); with the fake's equity at 70,000 on a 100,000 baseline, `trading_paused = true`, and `config_path` pointing at a missing file, an approved sell is still submitted as a market order; no `account_snapshots` row is written for an exit (FR-004 applies to buys only)

### Implementation for User Story 2

- [X] T024 [US2] Implement `check_exit` in `src/trading_agent/execution/checks.py` (E6 exit table) and route sells and stop-loss exits to it in `service.py`, computing held qty from `broker.get_positions()` and open sell qty from Execution's own non-final sell orders for the symbol (joined to `approved_order` for symbol, side and qty, as in T020); exits read no pause, baseline, account or config
- [X] T025 [US2] Run the US2 tests; confirm T022 and T023 pass, and US1's still do

**Checkpoint**: exits are submitted under every stop, and only for shares held.

---

## Phase 5: User Story 3 - No order is ever placed twice, even across a crash (Priority: P1)

**Goal**: At most one broker order per approved verdict, whatever step a crash interrupts.

**Independent Test**: Inject a failure at every step of a submission, re-run `tick()` against the
same fake broker, and count submissions per client id.

### Tests for User Story 3 ⚠️ write first, confirm they fail

- [X] T026 [P] [US3] Write `tests/integration/execution/test_crash_recovery.py`, parametrized over the crash points of E5 (before `find_order`; after `find_order`, before `submit_order`; `submit_order` raising `BrokerUnavailable` with `after_effect=True`; after `submit_order` returned but before the `orders` insert, by monkeypatching the insert helper to raise; after the insert, before commit) for both a buy and a sell: the first `tick` raises or retries, a second `tick` completes, and in every case the fake has **exactly one** submission for the verdict's client id and the database has exactly one `orders` row matching it (SC-002). Also: (a) a verdict whose order id collides with another verdict's existing order, seeded in **both** the database and the fake → `identifier_clash` refusal, no submission, and no primary-key error (FR-008, analyze S6); (b) crash after submission just before the close, restart after the close → the sweep finds the order via `find_order` and records it, not `approval_expired` (analyze S2); (c) `submit_order` times out after placing and the fake's `find_order` lags: a tick less than 2 minutes later does **not** resubmit; with the lag lasting past 2 minutes, a resubmission is rejected by the fake (`reject_duplicate_client_ids=True`) and the double-check records the live order, not `rejected` (analyze S3, S7); with `reject_duplicate_client_ids=False` and the lookup caught up by then, exactly one submission exists; (d) a buy recovered by `find_order` has its `limit_price` set and counts toward open buy cost in the next buy's reserve check (analyze S4)
- [X] T027 [P] [US3] Write `tests/integration/execution/test_lapsed_approvals.py`: approved verdicts from 2026-09-25 with no outcome get an `approval_expired` refusal on the next tick and are never submitted (SC-001); a verdict from today still unsubmitted at 20:01 UTC gets `approval_expired`; one that already has an order or refusal gets nothing new; one whose order exists at the fake broker but not in the database gets that order recorded, not a refusal; with the broker unreachable the sweep leaves it for the next tick; every approved verdict with a trading day in the past has exactly one outcome after a tick (E4)

### Implementation for User Story 3

- [X] T028 [US3] In `src/trading_agent/execution/service.py`, factor the approval path into separately patchable steps (lookup, check, submit, record) with the transaction boundary exactly as E5, and add `_sweep_lapsed(conn, broker, now)` run in `tick` after sync: for each approved verdict with no outcome whose `trading_day < today`, or `= today` when `now` is at or after `calendar.close_time(today)`, take the same lock and run E5 steps 1–3 (clash check, `find_order`, record if found); only if the broker has no order insert an `approval_expired` refusal (details `trading_day`, `now`); on `BrokerUnavailable` leave it for the next tick (research E4)
- [X] T029 [US3] Run the US3 tests; confirm T026 and T027 pass

**Checkpoint**: crash at any point, restart, and the broker still has one order per approval.

---

## Phase 6: User Story 4 - Execution refuses to run against anything but the paper account (Priority: P1)

**Goal**: The paper-only guard of research E2, and the runner that calls it before anything else.

**Independent Test**: Construct the adapter with each configured address and a stubbed account
read; only the paper address with a successful read starts. No network.

### Tests for User Story 4 ⚠️ write first, confirm they fail

- [ ] T030 [P] [US4] Write `tests/unit/execution/test_paper_guard.py` (no network; the SDK's `TradingClient` and data client are monkeypatched with stand-ins that record their constructor arguments): `PAPER_TRADING_URL == "https://paper-api.alpaca.markets"`; with `ALPACA_BASE_URL` unset, the trading client is built with `paper=True` and `url_override=PAPER_TRADING_URL`; with it set to `https://api.alpaca.markets`, `https://paper-api.alpaca.markets.evil.com`, `http://paper-api.alpaca.markets`, or a trailing-slash variant, `NotPaperTrading` is raised **before** any client is constructed (assert no constructor call); `verify_paper()` raises `NotPaperTrading` when the stubbed account read raises (401, network error) and passes when it returns an account, logging the account number; a missing key variable raises `ConfigError` naming the variable, never its value
- [ ] T031 [P] [US4] Write `tests/unit/execution/test_runner.py`: `startup()` calls `verify_paper()` before any other broker or database call, and a `NotPaperTrading` from it propagates and no `tick` runs; `startup()` and `tick()` raise when the connection isn't autocommit (and `tick` accepts it with `_allow_savepoints=True`); the runner's loop exits non-zero when `tick` raises `psycopg.OperationalError` (stubbed), rather than logging and continuing (ADR 0013); the runner's module reads no `RISK_GATE_DATABASE_URL` (import-scan the source); `python -m trading_agent.execution` exits non-zero on `NotPaperTrading` (run `main()` with a stub adapter, not a subprocess)

### Implementation for User Story 4

- [ ] T032 [US4] Implement `src/trading_agent/execution/alpaca.py`: `PAPER_TRADING_URL` constant; `AlpacaBroker(key_id, secret, configured_base_url: str | None)` raising `NotPaperTrading` if `configured_base_url` is set and `!=` the constant (exact string comparison), then building `TradingClient(key_id, secret, paper=True, url_override=PAPER_TRADING_URL)` and `StockHistoricalDataClient(key_id, secret)`; `verify_paper()` (one `get_account()`, any exception → `NotPaperTrading`); the `Broker` methods using `get_account`, `get_all_positions`, `get_stock_latest_quote`/`get_stock_latest_trade` with `feed=DataFeed.IEX`, `get_order_by_client_id` (404 → `None`), `get_order_by_id`, `submit_order` with `LimitOrderRequest`/`MarketOrderRequest`, `TimeInForce.DAY`, and `client_order_id`; converting every value to the `broker.py` types with `Decimal(str(...))`; mapping SDK API errors with status 403/422 on submission to `OrderRejected(message)` and everything else (timeouts, 5xx, connection errors) to `BrokerUnavailable`; no retries (contracts/broker-port.md)
- [ ] T033 [US4] Implement `startup(broker, conn)` in `service.py` (guard, then one order sync and reconciliation once US7 exists; until then, the guard only) and the runner `src/trading_agent/execution/__main__.py`: read `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, optional `ALPACA_BASE_URL` and `EXECUTION_DATABASE_URL` via `storage.db.require_env` (never `RISK_GATE_DATABASE_URL`); open one `psycopg.connect(url, autocommit=True, row_factory=dict_row)` (not `storage.db.connect`, which commits only on close); build the adapter; `startup` (which raises unless `conn.autocommit`); then every 60 seconds `tick(datetime.now(UTC), ...)`; log each `TickReport`; exit non-zero on `NotPaperTrading` and on `psycopg.OperationalError` or a closed connection, so the platform restarts it (ADR 0013)
- [ ] T034 [US4] Run the US4 tests; confirm T030 and T031 pass

**Checkpoint**: nothing starts unless it's pointed at the paper account.

---

## Phase 7: User Story 7 - Positions and orders reflect confirmed fills (Priority: P2)

Built before US5 and US6 because the stop-loss monitor measures from `positions`, which must be
synced first (E8).

**Goal**: Every non-final order is followed to a final state, and `positions` matches the broker.

**Independent Test**: With the fake broker, drive fills, partial fills, close-outs and rejections
through `tick()` and compare `orders` and `positions` with the fake after each step.

### Tests for User Story 7 ⚠️ write first, confirm they fail

- [ ] T035 [P] [US7] Write `tests/unit/execution/test_fills.py`: `map_status(raw, filled_qty)` for every broker status in the E7 table (including `new` with a partial fill → `partially_filled`, `done_for_day` → `expired`, `replaced` → raises `UnexpectedStatus`); `is_final` true exactly for `filled`, `rejected`, `canceled`, `expired`; `fill_delta(f0, p0, f1, p1)` → (6, 210) for 24 @ 200 then 30 @ 202; `apply_fill(position, side, dq, dp)`: none + buy 24 @ 201.50 → 24 @ 201.50; 24 @ 200 + buy 6 @ 210 → 30 @ 202; 50 @ 200 − sell 20 → 30 @ 200 (avg unchanged); sell of the whole holding → `None` (delete); average rounded to 4 decimals only when stored
- [ ] T036 [P] [US7] Write `tests/integration/execution/test_fills_and_reconcile.py`: a submitted buy filled fully by the fake → next tick sets `status = 'filled'`, `fill_qty`, `fill_price`, and creates the position; filled 10 of 24 then `close_session()` → `partially_filled` then `expired`, position 10; an order rejected at submission is `rejected` with `broker_reason` and never resubmitted; a buy on top of 24 @ 200 filled 6 @ 210 → 30 @ 202; a full sell deletes the row; a position row that disagrees with the fake (qty or avg) is overwritten with the fake's values and a warning with both values is logged (`caplog`); a broker position missing from the table is inserted, and a table row the broker doesn't hold is deleted (SC-007); order and position updates for one fill are in one transaction (a failure between them leaves neither)

### Implementation for User Story 7

- [ ] T037 [US7] Implement `src/trading_agent/execution/fills.py` (pure) to make T035 pass: `map_status`, `is_final`, `fill_delta`, `apply_fill`, `UnexpectedStatus`
- [ ] T038 [US7] In `service.py` add `_sync_orders(conn, broker, now)` (every non-final order: `get_order`, apply the fill delta to `positions` and update the order, one transaction per order; `UnexpectedStatus` logged as an error and the row left unchanged) and `_reconcile_positions(conn, broker)` (E8), both run first in `tick`, and call them from `startup` too (FR-009)
- [ ] T039 [US7] Run the US7 tests; confirm T035 and T036 pass, and the earlier stories' still do

**Checkpoint**: `orders` and `positions` track the broker.

---

## Phase 8: User Story 5 - Held positions are checked against the stop-loss line every 30 minutes (Priority: P2)

**Goal**: The monitor records triggers only at or below the line; the gate evaluates them in its own
process (spec Clarifications 2026-09-28); Execution submits only approved exits.

**Independent Test**: Seed positions and last trades above, at and below the line; run `tick()`
and the gate's trigger runner between ticks; check triggers, verdicts, and the fake's submissions.

### Tests for User Story 5 ⚠️ write first, confirm they fail

- [ ] T040 [P] [US5] Write `tests/unit/execution/test_monitor.py` for `breaches(positions, trades, stop_loss_pct, now, open_sell_symbols) -> list[(symbol, price, observed_at)]`: 50 @ 200 with a trade at 160 → breach at 160; 161 → none; a trade older than 30 minutes → skipped (and reported as stale); a symbol with an open sell order, an unevaluated trigger from today, or an approved exit with no outcome → skipped; a missing trade → skipped and reported as failed; the line is `avg × (1 − stop_loss_pct/100)` in `Decimal`
- [ ] T041 [P] [US5] Write `tests/unit/execution/test_schedule.py` (the monitor part): on 2026-09-28 the windows start at 13:30, 14:00, … 19:30 UTC; at 14:10 with no check yet in the 14:00 window → due; after a *successful* check in that window → not due; after a check where any held position's price fetch failed or was stale → still due (analyze U2); nothing is due before the open, after the close, on 2026-09-26, or on 2026-11-26; on 2026-11-27 the last window is 17:30–18:00 UTC
- [ ] T042 [P] [US5] Add `gate_runner_pass(conn, now)` to `tests/integration/execution/conftest.py` (runs `evaluate_pending_triggers(conn, now, REPO_CONFIG)` inside `as_role(conn, "ta_risk_gate")`, imported inside the helper), then write `tests/integration/execution/test_stop_loss_monitor.py` with the real gate's runner run between ticks: 50 AAPL @ 200 in `positions` and the fake, last trade 160 → tick 1 records one `stop_loss_triggers` row at 160 and submits nothing; the gate runner pass records an approved verdict; tick 2 submits a market sell of 50; last trade 161 → no trigger; the gate rejects (position avg changed so 160 isn't a breach) → no submission; an open sell for the symbol → no new trigger; the same window twice → one successful check; a window whose trade fetch failed is retried on the next tick; a trade stale for two consecutive windows logs at error level; with `config_path` missing → no trigger recorded, an error-level log naming the setting and saying the stop-loss monitor is off (`caplog`), and the tick still processes approved exits (E12, analyze U3); the runner's gate pass ignores triggers from an earlier trading day; with no gate pass for 6 minutes after a trigger, the next tick logs the "unevaluated" error and reports it in `TickReport`, and records no second trigger for that symbol

### Implementation for User Story 5

- [ ] T043 [US5] Implement `src/trading_agent/execution/monitor.py` (`breaches`, `MAX_TRADE_AGE = timedelta(minutes=30)`) and the monitor half of `src/trading_agent/execution/schedule.py` (`monitor_window(now) -> (start, end) | None` from `trading_agent.risk.calendar.open_time`/`close_time`, early closes included)
- [ ] T044 [US5] In `service.py` add `_run_monitor(conn, broker, now, config)`: if `config` failed to load, log at error level (naming the setting; "stop-loss monitor is off") and return; skip if the window already had a successful check (in-memory set of window starts, owned by the object that holds `tick`'s state); after sync/reconcile, fetch last trades for every held symbol, compute breaches, and for each insert a `stop_loss_triggers` row in its own `conn.transaction()` (committed, so the gate's process sees it). Execution does **not** evaluate it; the approved exit is picked up by a later tick's approval pass. Mark the window done only if every price check succeeded; track consecutive stale windows per symbol and log at error level at two. Also, every tick (not only when the monitor runs), count triggers from today with no verdict older than 5 minutes, log each at error level ("stop-loss trigger unevaluated; is the gate's trigger runner running?") and add the count to `TickReport` (ADR 0013, analyze P2)
- [ ] T045 [P] [US5] Write tests first in `tests/integration/risk/test_trigger_runner.py`: `evaluate_pending_triggers(conn, now, config_path)` as `ta_risk_gate` evaluates exactly the triggers observed on `now`'s trading day with no verdict, returns their count, is idempotent, and skips (and logs) triggers from an earlier trading day; a `RiskConfigError` is logged and nothing is written; one trigger whose evaluation raises (monkeypatch `evaluate_stop_loss_trigger` to raise for one id) is logged and the others are still evaluated; `psycopg.OperationalError` propagates (the loop's exit is tested in `tests/unit/risk/test_trigger_runner_main.py`: the `__main__` loop exits non-zero on it)
- [ ] T046 [US5] Implement the gate's trigger runner (hosting only, no gate logic): `evaluate_pending_triggers(conn, now, config_path)` in `src/trading_agent/risk/runner.py` calling `trading_agent.risk.service.evaluate_stop_loss_trigger` per pending trigger, each in its own `try` block that logs and continues (except `psycopg.OperationalError`, which propagates), and `src/trading_agent/risk/__main__.py` reading only `RISK_GATE_DATABASE_URL`, opening an autocommit connection, calling it every 60 seconds, and exiting non-zero on `psycopg.OperationalError` or a closed connection (ADR 0013). Widen `tests/unit/risk/test_config_import_guard.py` if needed so the runner may import `risk.service`; the pure-core rules are unchanged
- [ ] T047 [US5] Run the US5 tests; confirm T040–T042 and T045 pass

**Checkpoint**: a 20% drop is caught within a window and exited through the gate.

---

## Phase 9: User Story 6 - Account state is recorded before every open and before every buy (Priority: P2)

**Goal**: One pre-open snapshot every trading day, so the gate has a baseline, and Execution's
baseline equals the gate's.

**Independent Test**: Run `tick()` across a pre-open window, a weekend and a holiday; check
snapshots; then evaluate a buy through the real gate and compare baselines.

### Tests for User Story 6 ⚠️ write first, confirm they fail

- [ ] T048 [P] [US6] Extend `tests/unit/execution/test_schedule.py` with `pre_open_due(now, has_snapshot_before_open_today)`: on 2026-09-28 due from 12:30 to 13:29:59 UTC when none exists, not due at 12:29 or at/after 13:30, not due once one exists; never due on 2026-09-26 or 2026-11-26; on 2026-11-27 the window is relative to that day's open
- [ ] T049 [P] [US6] Write `tests/integration/execution/test_pre_open_snapshot.py`: a tick at 13:00 UTC records exactly one snapshot with the fake's values; a second tick at 13:10 records none; a tick where `get_account` fails records none and the next succeeds; no snapshot on a weekend or holiday tick; after the pre-open snapshot, `evaluate_decision` for a buy (real gate) records `system_state.daily_starting_equity` equal to Execution's own baseline computation for the same day (E11)

### Implementation for User Story 6

- [ ] T050 [US6] Implement `pre_open_due` in `schedule.py` (window `[open − 60 min, open)`) and `_record_pre_open_snapshot(conn, broker, now)` in `service.py`, run last in `tick`; the "exists" check queries `account_snapshots` for today's New York date before today's open (so it survives restarts)
- [ ] T051 [US6] Run the US6 tests; confirm T048 and T049 pass

**Checkpoint**: every story is in place; `tick` runs every duty in the E13 order.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [ ] T052 [P] Write `tests/unit/execution/test_properties.py` (Hypothesis, `max_examples=10_000`): over generated live states (equity, cash, held qty, open buy qty and cost, ask, qty, ceiling, baseline) biased so most reach the limit checks, every `Submit` from `check_buy` satisfies `(held + open + qty) × limit_price ≤ max_position_pct% × equity` and `cash − open_cost − qty × limit_price ≥ cash_reserve_pct% × equity` and `limit_price ≤ ceiling` (SC-004); `check_buy` is deterministic (same input twice → equal outcome); `check_exit` never refuses for any reason outside `{APPROVAL_EXPIRED, IDENTIFIER_CLASH, SHARES_HELD_DIFFER}`. Add a guard test that fails if fewer than 500 generated cases end in `Submit` (the 002 lesson: a property that never reaches its branch checks nothing)
- [ ] T053 [P] Write `tests/integration/execution/test_tick_order.py`: one `tick` with a filled order to sync, a lapsed approval, a new buy approval, a due monitor window and (at 13:00 UTC on a different day fixture) a due pre-open snapshot, asserting the E13 order via the fake's call log and the returned `TickReport` counts; that approved exits are submitted before approved buys in the same tick; and that an unexpected error while processing one approval (monkeypatch a step to raise for one verdict id) or syncing one order is logged and every other unit in the tick still completes (analyze S5)
- [ ] T054 [P] Write `tests/integration/execution/test_committed_connections.py` (analyze S1): on a fresh database from `make_database` (migrated, dropped at the end), two **separate autocommit** connections, one `SET ROLE ta_execution`, one `SET ROLE ta_risk_gate`; seed a held position breaching its stop and the fake broker; run `tick` on the first, `evaluate_pending_triggers` on the second, `tick` again on the first → exactly one market sell submitted, and the trigger, verdict and order rows are visible from a third connection (proving they were committed); and `startup` refuses a non-autocommit connection
- [ ] T055 [P] Update `docs/specs/execution.md` per ADR 0012 and this feature's Clarifications: the order identifier format, refusals recorded in `execution_refusals` (with a link to `specs/003-execution/contracts/refusal-reasons.md`), the pause check on buys, the paper-only guard as built (research E2, including that no documented account field marks paper), IEX prices and the last-trade stop-loss price, and Execution finding approvals itself each tick; and in `specs/002-risk-gate/contracts/gate-interface.md` and `docs/specs/risk-gate.md`, name the gate's trigger runner (`python -m trading_agent.risk`) as the caller of `evaluate_stop_loss_trigger`, citing this feature's Clarifications 2026-09-28 and ADR 0013; and add a "Running" section to `specs/003-execution/quickstart.md` listing the two long-running processes (`python -m trading_agent.execution`, `python -m trading_agent.risk`), each one's environment variables, that each must run with only its own credentials, and that whichever feature first writes the Railway configuration must start both (ADR 0013 Consequences)
- [ ] T056 [P] Update `docs/specs/data-model.md`: the `orders.id` row (ADR 0012 format), the `expired` status, `limit_price` and `broker_reason`, and a new `execution_refusals` section; and `docs/architecture/overview.md` if it names the old identifier
- [ ] T057 Mutation check (quickstart §4): one at a time, (a) compare the ask with `>=` instead of `>`, (b) drop open buy cost from the reserve check, (c) skip the `find_order` lookup before submitting, (d) apply the daily-loss check to exits, (e) grant `ta_execution` SELECT on `system_state.halt_triggered_on` in the migration, (f) drop the since-the-open lookback from row 7, (g) let the lapsed sweep skip `find_order`, (h) record `rejected` without the second `find_order`; confirm at least one test fails for each, then revert. Record the results under "Implementation notes" below
- [ ] T058 Full validation per `quickstart.md`: offline suite, integration suite (expect 686 + this feature's tests), `ruff check src tests`, `ruff format --check src tests`; confirm the fake broker's and the network guard's tests ran (not skipped)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → stories
- **US1 (Phase 3)**: after Foundational. Introduces `check_buy`, `tick`, and the approval path.
- **US2 (Phase 4)**: after US1 (same approval path, exit branch)
- **US3 (Phase 5)**: after US1 and US2 (crash tests cover both branches; refactors the path into steps)
- **US4 (Phase 6)**: after Foundational only for the adapter (T030, T032); the runner (T033) after US1. Can run in parallel with US2/US3 by a second person, since it touches `alpaca.py` and `__main__.py`
- **US7 (Phase 7)**: after US1 (needs submitted orders to follow)
- **US5 (Phase 8)**: after US2 (exit path) and US7 (positions synced before the monitor)
- **US6 (Phase 9)**: after US1 (the pre-buy snapshot exists; this adds the scheduled one)
- **Polish (Phase 10)**: after all stories

`service.py` is edited by US1, US2, US3, US5, US6 and US7, so those run in sequence.

### Within Each Story

- Test tasks first ([P] with each other), confirmed failing for the right reason
- Then implementation tasks, in order
- Then the run-and-confirm task

### Parallel Opportunities

- Setup: T002 and T003 alongside T001
- Foundational: T004–T010, T014 and T015 are all [P]; T012 follows T011; T013 follows T012
- Test tasks per story: (T017, T018), (T022, T023), (T026, T027), (T030, T031), (T035, T036), (T040, T041, T042, T045), (T048, T049)
- US4's adapter (T030, T032) alongside US2/US3
- Polish: T052–T056

---

## Parallel Example: User Story 5

```bash
Task: "Write tests/unit/execution/test_monitor.py"
Task: "Write tests/unit/execution/test_schedule.py (monitor windows)"
Task: "Write tests/integration/execution/test_stop_loss_monitor.py"
Task: "Write tests/integration/risk/test_trigger_runner.py"
# then, once they fail for the right reason:
Task: "Implement monitor.py and the monitor half of schedule.py"
```

---

## Implementation Strategy

### MVP (User Story 1)

Setup → Foundational → US1: approved buys become limit orders at the live ask only when live
numbers confirm them, against the fake broker. **Stop and validate** with the US1 tests.

### Incremental Delivery

1. US1: buys with every live check
2. US2: exits, never blocked by equity or pause
3. US3: crash safety and lapsed approvals, so every approval has one outcome
4. US4: the paper-only guard and the runner
5. US7: fills and reconciliation
6. US5: the stop-loss monitor, and the gate's own trigger runner
7. US6: the pre-open snapshot
8. Polish: properties, docs, mutation check

## Notes

- Nothing in this list runs Execution against the real paper account. Doing so, even once to
  smoke-test the adapter, is a separate step the owner must ask for explicitly.
- A failing property test is a real counterexample: fix the code, then add the shrunk input as an
  example test.
- Commit after each phase checkpoint.
- Never point `TEST_DATABASE_URL` at the Railway database.

## Implementation notes

(Filled in during `/speckit-implement`.)
