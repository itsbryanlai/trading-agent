---

description: "Task list for the Risk Gate (feature 002)"
---

# Tasks: Risk Gate

**Input**: Design documents from `/specs/002-risk-gate/`

**Prerequisites**: plan.md, spec.md, research.md (G1–G15), data-model.md,
contracts/gate-interface.md, contracts/rejection-rules.md, contracts/risk-config.md, quickstart.md.
The shared grants contract is `specs/001-data-model/contracts/role-grants.md`, amended for this
feature.

**Tests**: Included, and written first within each story. The spec's success criteria are
universal claims ("no approved order ever breaches", "identical inputs → identical verdict"), so
the example tests are backed by Hypothesis property tests in Polish (G15).

**Organization**: One phase per user story (US1–US5 in spec.md). The pure core grows rule by rule
across stories; the service is introduced in US1 and extended by US3 and US4.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5

## Conventions every task follows

- **Money, prices, quantities and percentages are `decimal.Decimal`**, never `float` (G2). Config
  numbers convert to `Decimal` on load; database `numeric` already arrives as `Decimal`.
- **The pure core** (`risk/model.py`, `risk/rules.py`, `risk/config.py`, `risk/gate.py`) never
  imports `psycopg`, `exchange_calendars`, `os`, or `risk/service.py`, and never reads the clock or
  the environment (G1, FR-002). `now`, `trading_day` and `market_open` arrive as inputs.
- **Rule names** are only ever referenced through the constants in `risk/rules.py`. The strings
  match `contracts/rejection-rules.md` exactly.
- **Service functions take a `psycopg.Connection`** already connected as (or `SET ROLE` to)
  `ta_risk_gate`. They run inside `conn.transaction()`, and accept `now: datetime | None` and
  `config_path: Path` so tests can fix both.
- **Test clock**: unit and integration tests use a fixed NYSE session minute, **Monday
  2026-09-28 14:00 UTC (10:00 ET)**, unless the test is about another time. Weekend:
  2026-09-26. Holiday: 2026-11-26 (Thanksgiving). Early close: 2026-11-27 (13:00 ET).
- A committed migration is never edited; `0006` is new.

---

## Phase 1: Setup

- [ ] T001 Add dependencies to `pyproject.toml`: runtime `PyYAML>=6.0,<7` and `exchange-calendars>=4.13,<5`, dev extra `hypothesis>=6`; add `"trading_agent.risk"` to nothing else (the config file is read by path, not packaged). Reinstall with `uv pip install --python .venv/bin/python -e ".[dev]"` and confirm `import exchange_calendars, yaml, hypothesis` works
- [ ] T002 [P] Create `config/risk.yaml` with exactly the schema and default values in `contracts/risk-config.md`: `max_position_pct: 8`, `cash_reserve_pct: 20`, `stop_loss_pct: 20`, `max_orders_per_day: 5`, `daily_loss_halt_pct: 20`, `max_buy_price_tolerance_pct: 1`, and `universe` with `listing: us_common_equity`, `min_market_cap_usd: 500000000`, `min_avg_daily_dollar_volume_usd: 10000000`, `min_share_price_usd: 5`; with a header comment pointing to `contracts/risk-config.md` and `docs/policy/agent-management.md` (changes are code-reviewed; no agent writes this file)
- [ ] T003 [P] Create packages: `src/trading_agent/risk/__init__.py`, `tests/unit/risk/__init__.py`, `tests/integration/risk/__init__.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The vocabulary (rule names, value types), the config loader, the calendar, and
migration `0006`. Every story depends on these.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T004 [P] Write `src/trading_agent/risk/rules.py`: one string constant per rule in `contracts/rejection-rules.md` (`MARKET_CLOSED = "market_closed"`, `NO_POSITION`, `STOP_LOSS_NOT_BREACHED`, `DIRECTION_CONTRADICTS_TARGET`, `TARGET_ALREADY_MET`, `TRADING_PAUSED`, `NO_ACCOUNT_SNAPSHOT_TODAY`, `NO_DAILY_BASELINE`, `DAILY_LOSS_HALT`, `DAILY_ORDER_CAP`, `UNIVERSE_NO_REFERENCE_DATA`, `UNIVERSE_LISTING`, `UNIVERSE_MARKET_CAP`, `UNIVERSE_DOLLAR_VOLUME`, `UNIVERSE_SHARE_PRICE`, `MAX_POSITION_PCT`, `CASH_RESERVE_PCT`), plus `US_LISTED_MICS = frozenset({"XNYS", "XNAS", "XASE"})` (G14)
- [ ] T005 [P] Write `src/trading_agent/risk/model.py`, frozen dataclasses with `Decimal` fields, matching `contracts/gate-interface.md`: `DecisionRequest(symbol, direction: Literal["buy","sell"], target_weight_pct, quote)`; `StopLossRequest(symbol, observed_price)`; `Reference(security_type, exchange_mic, market_cap_usd, avg_daily_dollar_volume_usd, share_price_usd)`; `Context(now, trading_day: date, market_open: bool, trading_paused: bool, halt_active: bool, shares_held: int, avg_entry_price: Decimal | None, equity: Decimal | None, cash: Decimal | None, baseline_equity: Decimal | None, increase_orders_approved_today: int, reference: Reference | None)`; `ApprovedOrder` with exactly the keys of data-model.md's `approved_order` shape (`symbol, side, qty: int, order_type, limit_price: Decimal | None, time_in_force="day", trading_day, exposure, trims: tuple[str, ...], source`) and `to_json() -> dict` (Decimals rendered as strings, date as ISO); `Verdict(approved: bool, rejection_rule: str | None, order: ApprovedOrder | None)` with the invariant that exactly one of `rejection_rule` / `order` is set; `GateResult(verdict, record_halt: bool, config_version: str, trading_day: date)`
- [ ] T006 [P] Write tests first in `tests/unit/risk/test_config.py` (use `tmp_path`): the repo's `config/risk.yaml` loads and every value equals the contract; the version is 12 lowercase hex characters and changes when a comment changes; each failure raises `RiskConfigError` whose message contains the dotted setting path: missing file; invalid YAML; missing key (each top-level key and each `universe.*` key); unknown key at either level (e.g. `max_postion_pct`); wrong type (`max_orders_per_day: "5"`, `stop_loss_pct: true`); out of range (`max_position_pct: 0` and `101`, `cash_reserve_pct: 100`, `stop_loss_pct: 100`, `daily_loss_halt_pct: 0`, `max_orders_per_day: -1`, `max_buy_price_tolerance_pct: 11`, `universe.min_share_price_usd: 0`, `universe.listing: all`); and no loaded numeric value is a `float`
- [ ] T007 Implement `src/trading_agent/risk/config.py` to make T006 pass: `RiskConfig` and `UniverseConfig` frozen dataclasses; `load_config(path: Path) -> RiskConfig` using `yaml.safe_load`, exact key sets, ranges exactly as in `contracts/risk-config.md` (`max_position_pct` (0,100]; `cash_reserve_pct` [0,100); `stop_loss_pct` (0,100); `max_orders_per_day` int ≥ 0 and not bool; `daily_loss_halt_pct` (0,100); `max_buy_price_tolerance_pct` [0,10]; `universe.listing == "us_common_equity"`; `min_market_cap_usd` ≥ 0; `min_avg_daily_dollar_volume_usd` ≥ 0; `min_share_price_usd` > 0), converting numbers via `Decimal(str(value))`; `config_version = sha256(raw_bytes).hexdigest()[:12]` stored on the `RiskConfig`; `RiskConfigError(Exception)`
- [ ] T008 [P] Write tests first in `tests/unit/risk/test_calendar.py`: 2026-09-28 14:00 UTC is open and its trading day is 2026-09-28; 2026-09-28 13:29 UTC (09:29 ET) is closed; 2026-09-26 (Saturday) closed; 2026-11-26 (Thanksgiving) closed; 2026-11-27 17:59 UTC (12:59 ET) open and 18:01 UTC (13:01 ET, after the early close) closed; `open_time(2026-09-28)` is 13:30 UTC; a naive datetime raises `ValueError`
- [ ] T009 Implement `src/trading_agent/risk/calendar.py` to make T008 pass, wrapping `exchange_calendars.get_calendar("XNYS")` (created once, module-level): `market_open(now) -> bool`, `trading_day(now) -> date` (the New York date of `now`), `open_time(day) -> datetime` (UTC, tz-aware; raises if `day` isn't a session). Timezone-aware datetimes only
- [ ] T010 [P] Write `tests/unit/risk/test_config_import_guard.py`: walk every `.py` under `src/trading_agent/` with `ast`; assert that `trading_agent.risk.config` is imported only by modules inside `trading_agent/risk/`; and that `risk/gate.py`, `risk/model.py`, `risk/rules.py`, `risk/config.py` import none of `psycopg`, `exchange_calendars`, `os`, `trading_agent.risk.service`, or `trading_agent.risk.calendar`, and never call `datetime.now()` / `date.today()` (G1, G15)
- [ ] T011 [P] Write `tests/unit/risk/builders.py`: `context(**overrides) -> Context` defaulting to a healthy open market (the test clock above, `trading_day=2026-09-28`, `market_open=True`, not paused, no halt, no position, `equity=Decimal("100000")`, `cash=Decimal("100000")`, `baseline_equity=Decimal("100000")`, `increase_orders_approved_today=0`, a passing `Reference("common_stock", "XNAS", 3e12, 5e9, 200)` as Decimals); `buy(symbol="AAPL", target="5", quote="200")`, `sell(...)`, `trigger(symbol, observed)`; and `config(**overrides)` returning the repo config with overrides
- [ ] T012 Extend the storage tests for migration `0006`, written before it: in `tests/integration/storage/grants_matrix.py` add `"ta_reference_data"` to `ROLES` and the rows for `stop_loss_triggers` (`ta_execution` {S, I}; `ta_risk_gate`, `ta_journal`, `ta_assistant`, `ta_dashboard` {S}) and `instrument_reference` (`ta_reference_data` {S, I, U}; `ta_risk_gate`, `ta_assistant`, `ta_dashboard` {S}), exactly per the amended `specs/001-data-model/contracts/role-grants.md`; in `factories.py` add `ObjectSpec`s (`stop_loss_triggers` probe `observed_price`, `instrument_reference` probe `exchange_mic`); update `tests/integration/storage/chain.py` `insert_verdict` to supply `trading_day` and `config_version`; add `tests/integration/storage/test_verdict_sources.py`: a verdict with only a decision is accepted, with only a trigger is accepted, with both or neither fails `23514`; a second verdict for the same trigger fails `23505`; `trading_day` and `config_version` are NOT NULL; an order can reference a trigger's approved verdict; `stop_loss_triggers.observed_price = 0` and `instrument_reference.security_type = 'warrant'` fail `23514`
- [ ] T013 Write `src/trading_agent/storage/migrations/0006_risk_gate.sql` per `data-model.md`: create role `ta_reference_data NOLOGIN` if absent (same `DO` pattern as `0001`) and `GRANT USAGE ON SCHEMA public` to it; `stop_loss_triggers (id uuid PK DEFAULT gen_random_uuid(), symbol text NOT NULL, observed_price numeric(14,4) NOT NULL CHECK (observed_price > 0), observed_at timestamptz NOT NULL DEFAULT now())` with index `(observed_at DESC)`; `instrument_reference (symbol text NOT NULL, trading_day date NOT NULL, security_type text NOT NULL CHECK (security_type IN ('common_stock','etf','adr','other')), exchange_mic text NOT NULL, market_cap_usd numeric(20,2) NOT NULL CHECK (>= 0), avg_daily_dollar_volume_usd numeric(20,2) NOT NULL CHECK (>= 0), share_price_usd numeric(14,4) NOT NULL CHECK (> 0), fetched_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (symbol, trading_day))`; on `risk_verdicts`: `ALTER COLUMN decision_id DROP NOT NULL`; add `stop_loss_trigger_id uuid UNIQUE REFERENCES stop_loss_triggers (id)`; add `CHECK (num_nonnulls(decision_id, stop_loss_trigger_id) = 1)`; add `trading_day date`, backfill `(evaluated_at AT TIME ZONE 'America/New_York')::date`, then `SET NOT NULL`; add `config_version text`, backfill `'pre-002'`, then `SET NOT NULL`; index `(trading_day, verdict)`; grants exactly per the amended contract
- [ ] T014 Run `python -m pytest tests/ -q` and `python -m pytest tests/integration -m integration -q` (with `TEST_DATABASE_URL`); confirm T006, T008, T010, T012 pass along with all of feature 001's tests

**Checkpoint**: config, calendar, and schema are in place; the grants catalog test still proves
database = contract.

---

## Phase 3: User Story 1 - Every buy is sized inside the limits, or rejected with a reason (Priority: P1) 🎯 MVP

**Goal**: A buy or sell decision becomes one recorded verdict: a target-weight-sized order within
the position ceiling and cash reserve, or a rejection naming the rule.

**Independent Test**: Unit-evaluate the spec's US1 scenarios with exact quantities; then, through
the service against a real database, see the verdict written with `trading_day` and
`config_version`, returned unchanged on a second call, and nothing written for a hold.

### Tests for User Story 1 ⚠️ write first, confirm they fail

- [ ] T015 [P] [US1] Write `tests/unit/risk/test_buy_sizing.py` (using `builders.py`): (1) equity 100,000, no position, buy target 5% at 200 → approved limit buy of **24** shares, `limit_price` 202.00, `order_type` "limit", `exposure` "increase", `trims` empty; (2) 6% held (30 shares at 200), target 10% → trimmed to the 8% ceiling valued at 202 (`floor(8000/202) − 30 = 9` shares), `trims == ("max_position_pct",)`; (3) 40 shares at 200 (8%), target 10% → rejected `max_position_pct`; (4) cash 20,000 with equity 100,000 (at the 20% floor), buy → rejected `cash_reserve_pct`; cash 20,808 → approved 4 shares trimmed by `cash_reserve_pct`; (5) 50 held, sell target 0 → market sell 50, `limit_price` None, `exposure` "decrease"; sell target 2% on 50 held at 200 → keep `ceil(2000/200)=10`, sell 40; sell of unheld symbol → `no_position`; (6) 25 held at 200 (5%), buy target 5% → `target_already_met`; (7) buy target 2% with 25 held (5%) → `direction_contradicts_target`; sell target 8% with 25 held → `direction_contradicts_target`; (8) every approved order's `trading_day` equals the context's, and `GateResult.config_version` equals the config's
- [ ] T016 [P] [US1] Write `tests/integration/risk/conftest.py` and `tests/integration/risk/test_service_decisions.py`. Conftest fixtures (as admin, inside the rolled-back `conn`): `seed_open_day(conn)` inserts a pre-open account snapshot (2026-09-28 12:00 UTC, equity 100,000, cash 100,000) and one at 13:45 UTC, plus an `instrument_reference` row for AAPL on 2026-09-28 that passes the universe rules; `make_decision(conn, direction, target, quote)` inserts a report + decision; a `repo_config` path fixture. Tests call `evaluate_decision` inside `as_role(conn, "ta_risk_gate")` with `now` = the test clock: a buy writes exactly one verdict with `trading_day = 2026-09-28` and `config_version` = the config's, and an `approved_order` matching T015 case 1; a second call returns an equal verdict and the verdict count is unchanged (FR-017); a `hold` decision returns `None` and writes nothing; with `config_path` pointing at a missing file, `RiskConfigError` is raised and no verdict is written (FR-014)

### Implementation for User Story 1

- [ ] T017 [US1] Implement in `src/trading_agent/risk/gate.py` the decision path of `evaluate(request, context, config) -> GateResult`: `market_closed` first for every request; then, for sells, the exit rules in precedence (`no_position` → `no_account_snapshot_today` when the target is above 0% and `equity` is None → `direction_contradicts_target` → `target_already_met` → approve a market sell; a 0% target sells every share held and needs no equity); for buys, the sizing rules only (`direction_contradicts_target` → `target_already_met` → `max_position_pct` → `cash_reserve_pct` → approve with trims), using exactly the target-weight arithmetic in research.md G4 with the price ceiling `quote × (1 + tolerance/100)` (G3). Stop-loss requests raise `NotImplementedError` until US4. Leave clearly marked insertion points for US2/US3's buy-side stops at their precedence positions
- [ ] T018 [US1] Implement `evaluate_decision(conn, decision_id, *, now=None, config_path=Path("config/risk.yaml")) -> Verdict | None` in `src/trading_agent/risk/service.py`: load the config first (raising before any write); inside `conn.transaction()`, take `pg_advisory_xact_lock` with a fixed key (G10); return the existing verdict if one exists for the decision (G11); load the decision (return `None` for `hold`), the position, today's latest account snapshot (`taken_at` on `calendar.trading_day(now)` in New York), today's baseline via `system_state_effective`, falling back to the latest snapshot with `taken_at < calendar.open_time(today)` (via `choose_baseline`, stubbed to "stored or pre-open" here and completed in US3), `trading_paused` and `daily_loss_halt_active`, the count of today's approved `exposure = 'increase'` verdicts, and today's `instrument_reference` row; build the `Context` with `market_open` and `trading_day` from `risk/calendar.py`; call `gate.evaluate`; insert the verdict with `trading_day`, `config_version`, and `approved_order` as `to_json()`
- [ ] T019 [US1] Run the offline and integration suites; confirm T015–T016 pass

**Checkpoint**: MVP. Decisions become sized, recorded, idempotent verdicts.

---

## Phase 4: User Story 2 - Hard stops block new exposure but never block an exit (Priority: P1)

**Goal**: Market closed, pause, missing account data, halt, order cap, and universe each reject
buys; none but market-closed ever rejects an exit.

**Independent Test**: For each stop, one buy rejected naming it and one sell approved; the order
cap can't be exceeded by two concurrent evaluations.

### Tests for User Story 2 ⚠️ write first, confirm they fail

- [ ] T020 [P] [US2] Write `tests/unit/risk/test_hard_stops.py`, parametrized over each stop with a buy and a sell against the same context: `market_open=False` → buy and sell both `market_closed`; `trading_paused=True` → buy `trading_paused`, sell approved (FR-020); `equity=None` → buy `no_account_snapshot_today`, a sell to 0% approved for every share held, a sell to 2% rejected `no_account_snapshot_today` (FR-018); `baseline_equity=None` → buy `no_daily_baseline`, sell approved; `halt_active=True` → buy `daily_loss_halt`, sell approved; `increase_orders_approved_today=5` → buy `daily_order_cap`, sell approved; `reference=None` → `universe_no_reference_data`; `security_type="etf"` → `universe_listing`; `exchange_mic="OTCM"` → `universe_listing`; `market_cap_usd=499999999.99` → `universe_market_cap`; `avg_daily_dollar_volume_usd=9999999` → `universe_dollar_volume`; `share_price_usd=4.99` → `universe_share_price`; exactly-at-floor values (500,000,000 / 10,000,000 / 5) pass
- [ ] T021 [P] [US2] Write `tests/unit/risk/test_precedence.py`: contexts where two or more buy stops apply at once name the earlier rule in `contracts/rejection-rules.md` (e.g. paused + halt → `trading_paused`; halt + cap → `daily_loss_halt`; cap + bad universe → `daily_order_cap`; bad listing + low price → `universe_listing`; universe fail + ceiling full → the universe rule); market closed beats everything for both sides
- [ ] T022 [P] [US2] Write `tests/integration/risk/test_order_cap_race.py`, using `make_database` (a fresh migrated database, since this test must commit): seed 4 approved `exposure = 'increase'` verdicts for today and two buy decisions on different symbols with passing reference rows; run `evaluate_decision` for both on **two separate connections** (each `SET ROLE ta_risk_gate`) started together from two threads; assert exactly one is approved and the other rejected `daily_order_cap`

### Implementation for User Story 2

- [ ] T023 [US2] Add the buy-side stops to `src/trading_agent/risk/gate.py` at their precedence positions (research.md G5): `trading_paused` → `no_account_snapshot_today` → `no_daily_baseline` → `daily_loss_halt` (active flag only; crossing detection is US3) → `daily_order_cap` (`increase_orders_approved_today >= max_orders_per_day`) → `universe_no_reference_data` → `universe_listing` (`security_type == "common_stock"` and `exchange_mic in US_LISTED_MICS`) → `universe_market_cap` → `universe_dollar_volume` → `universe_share_price` (each floor inclusive: below the floor rejects). Sells skip all of them
- [ ] T024 [US2] Run the offline and integration suites; confirm T020–T022 pass

**Checkpoint**: US1 + US2 cover everything a single decision can hit.

---

## Phase 5: User Story 3 - A bad day trips the daily-loss halt automatically (Priority: P2)

**Goal**: The evaluation that finds equity at or below the loss line records the halt for today
and rejects the buy; the first evaluation of a day records the baseline from the pre-open snapshot.

**Independent Test**: With a $100,000 baseline, current equity $80,000 trips the halt and $80,001
doesn't; through the service, the halt and baseline are written to `system_state`, and yesterday's
halt reads inactive.

### Tests for User Story 3 ⚠️ write first, confirm they fail

- [ ] T025 [P] [US3] Write `tests/unit/risk/test_daily_loss.py`: baseline 100,000, equity 80,000 → buy rejected `daily_loss_halt` with `record_halt=True`; equity 80,001 → no halt, judged on the other rules; halt already active → `record_halt=False` (never re-recorded); a sell at equity 80,000 is approved **and** `record_halt=True` (detection runs on every evaluation; recording never blocks an exit); a stop-loss trigger at equity 80,000 is likewise approved with `record_halt=True`; with `baseline_equity=None` or `equity=None`, `record_halt=False`; `choose_baseline(stored=Decimal("99000"), pre_open=Decimal("100000"))` → `(99000, False)`; `choose_baseline(None, 100000)` → `(100000, True)`; `choose_baseline(None, None)` → `(None, False)`
- [ ] T026 [P] [US3] Write `tests/integration/risk/test_service_daily_loss.py`: with no baseline stored, the first `evaluate_decision` of the day records `baseline_trading_day = 2026-09-28` and `daily_starting_equity` = the pre-open snapshot's equity, and a second evaluation doesn't change it; with the latest snapshot at 80,000 against that baseline, a buy is rejected `daily_loss_halt` and `system_state.halt_triggered_on = 2026-09-28`, and no `positions` row changed; with no pre-open snapshot, a buy is rejected `no_daily_baseline` and nothing is recorded in `system_state`; with `halt_triggered_on` set to 2026-09-25, a buy on 2026-09-28 is not rejected for the halt

### Implementation for User Story 3

- [ ] T027 [US3] Add crossing detection to `src/trading_agent/risk/gate.py`. It runs on **every** evaluation, before precedence, whatever the request type: `record_halt = not halt_active and equity is not None and baseline_equity is not None and equity <= baseline_equity × (1 − daily_loss_halt_pct/100)` (FR-009, research.md G5). At the buy-side `daily_loss_halt` position, reject when `halt_active or record_halt`. Exits' verdicts are unaffected by it; implement `choose_baseline(stored_for_today, latest_snapshot_before_open)` per G8
- [ ] T028 [US3] Extend `src/trading_agent/risk/service.py` to persist the intents in the same transaction as the verdict: when `choose_baseline` says record, `UPDATE system_state SET baseline_trading_day, daily_starting_equity, updated_at`; when `record_halt`, `UPDATE system_state SET halt_triggered_on = <trading_day>, updated_at`. Both only through `ta_risk_gate`'s existing column grants
- [ ] T029 [US3] Run the offline and integration suites; confirm T025–T026 pass

---

## Phase 6: User Story 4 - Losing positions are exited at the stop-loss line (Priority: P2)

**Goal**: A stop-loss trigger is confirmed against the position's own entry price and approved as a
full market exit that no halt, pause, or cap can block.

**Independent Test**: A held position at an average entry of $200: a trigger observing $160 is
approved as a full sell even with the cap reached, the halt on, and trading paused; $160.01 is
rejected.

### Tests for User Story 4 ⚠️ write first, confirm they fail

- [ ] T030 [P] [US4] Write `tests/unit/risk/test_stop_loss.py`: 50 held at avg entry 200, `stop_loss_pct` 20: observed 160.00 → approved market sell of 50, `source` "stop_loss", `exposure` "decrease"; observed 160.01 → `stop_loss_not_breached`; observed 120 → approved; not held → `no_position`; with `increase_orders_approved_today=5`, `halt_active=True`, `trading_paused=True`, `equity=None`, `reference=None` all at once → still approved; `market_open=False` → `market_closed`
- [ ] T031 [P] [US4] Write `tests/integration/risk/test_service_stop_loss.py`: insert a position (50 @ 200) and a `stop_loss_triggers` row as `ta_execution`; `evaluate_stop_loss_trigger` as `ta_risk_gate` writes one verdict with `stop_loss_trigger_id` set and `decision_id` NULL, approved for 50 shares as a market sell; a second call returns it unchanged; a trigger at 161 is rejected `stop_loss_not_breached`; with 5 increase-approvals today and the halt recorded for today, a breaching trigger is still approved; the approved verdict can be referenced by an `orders` row inserted as `ta_execution`

### Implementation for User Story 4

- [ ] T032 [US4] Add the trigger path to `src/trading_agent/risk/gate.py`: `market_closed` → `no_position` → `stop_loss_not_breached` (approve only when `observed_price <= avg_entry_price × (1 − stop_loss_pct/100)`, entry from the context, never from the trigger) → approve a market sell of all `shares_held` with `source="stop_loss"`
- [ ] T033 [US4] Implement `evaluate_stop_loss_trigger(conn, trigger_id, *, now=None, config_path=...) -> Verdict` in `src/trading_agent/risk/service.py`, sharing the lock, idempotency lookup (by `stop_loss_trigger_id`), and context loading with `evaluate_decision`; insert the verdict with `stop_loss_trigger_id`
- [ ] T034 [US4] Run the offline and integration suites; confirm T030–T031 pass

---

## Phase 7: User Story 5 - Rules come from a reviewed file; a bad file stops trading (Priority: P3)

**Goal**: Every limit applied equals the file; a bad file approves nothing; every verdict names the
config it was judged against.

**Independent Test**: The service with a valid file, a missing file, and an out-of-range file:
only the first writes verdicts, and each verdict's `config_version` matches its file's hash.

- [ ] T035 [P] [US5] Write `tests/integration/risk/test_service_config.py`: with a temp config whose `max_position_pct` is 4, a buy targeting 5% is trimmed to 4% (the gate uses the file, not defaults); with an out-of-range file and with an unknown key, both `evaluate_decision` and `evaluate_stop_loss_trigger` raise `RiskConfigError` naming the setting and write nothing; two files differing only in a comment give different `config_version`s on their verdicts
- [ ] T036 [US5] Run the integration suite; confirm T035 passes (no implementation expected: T007 and T018 already provide it; any failure here is a real bug in those)

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T037 [P] Write `tests/unit/risk/test_properties.py` with Hypothesis (`@settings(max_examples=10_000, deadline=None)` on each property; strategies generate `Decimal` equity 1,000–10,000,000, cash 0–equity, quotes 1–5,000, target weights 0–100, holdings 0–10,000 shares, entry prices, counts 0–10, and random combinations of every stop and reference value): **SC-001**: every approved buy satisfies `(shares_held + qty) × limit_price <= max_position_pct/100 × equity` and `cash − qty × limit_price >= cash_reserve_pct/100 × equity`; every approved sell has `qty <= shares_held`; **SC-002**: calling `evaluate` twice on the same inputs returns equal `GateResult`s; **SC-003**: with the market open, any exit whose own conditions hold (position held; trigger breached; a partial sell has today's equity; direction agrees with target; target not already met) is approved regardless of pause, halt, cap, baseline, or reference data; any buy under any hard stop is rejected; and `record_halt` is true exactly when today's equity and baseline are known, equity is at or below the loss line, and the halt isn't already active, for every request type
- [ ] T038 [P] Write `tests/unit/risk/test_rules_contract.py`: parse the rule names out of the tables in `specs/002-risk-gate/contracts/rejection-rules.md` and assert they equal the set of constants in `risk/rules.py` (excluding `US_LISTED_MICS`), so the contract and the code can't drift
- [ ] T039 [P] Update `README.md`: the Status line now includes the Risk Gate; the layout lists `config/` and `src/trading_agent/risk/`; a short "Risk limits" paragraph points to `config/risk.yaml`, `specs/002-risk-gate/contracts/risk-config.md`, and the review rule in `docs/policy/agent-management.md`
- [ ] T040 [P] Run `ruff check src tests` and `ruff format --check src tests`; fix anything flagged
- [ ] T041 Walk through `specs/002-risk-gate/quickstart.md` against the `ta-pg` container: offline suite (including ≥10,000-example properties), integration suite, and the `\d risk_verdicts` spot check
- [ ] T042 Mutation check: temporarily change one limit comparison in `gate.py` (e.g. `<=` to `<` in the cash-reserve check) and confirm the property tests fail; temporarily add one extra grant in `0006` and confirm the grants catalog test fails; restore both
- [ ] T043 Cross-check the implementation against `contracts/*.md`, `data-model.md`, `docs/specs/risk-gate.md`, and `specs/001-data-model/contracts/role-grants.md`; update any document that implementation proved wrong, in the same commit (Constitution Principle V)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → stories
- **US1 (Phase 3)**: after Foundational. It introduces `gate.evaluate` and the service.
- **US2 (Phase 4)**: after US1 (adds buy stops to the same function; the race test needs the service)
- **US3 (Phase 5)**: after US2 (crossing detection sits at the `daily_loss_halt` precedence position US2 creates)
- **US4 (Phase 6)**: after US1 (the trigger path and the second service entry point); independent of US2/US3's code, but its test asserts exits pass their stops, so run it after US2/US3
- **US5 (Phase 7)**: after US1 and US4 (tests both entry points)
- **Polish (Phase 8)**: after all stories. The property tests need every rule to exist.

All stories edit `gate.py` and `service.py`, so they run in sequence, not in parallel.

### Within Each Story

- Test tasks first ([P] with each other), confirm they fail for the right reason
- Then the implementation tasks, in order
- Then the run-and-confirm task

### Parallel Opportunities

- Setup: T002 and T003 alongside T001
- Foundational: T004, T005, T006, T008, T010, T011, T012 are all [P]; T007 follows T006, T009 follows T008, T013 follows T012
- Each story's test tasks: (T015, T016), (T020, T021, T022), (T025, T026), (T030, T031)
- Polish: T037–T040

---

## Parallel Example: User Story 2

```bash
Task: "Write tests/unit/risk/test_hard_stops.py"
Task: "Write tests/unit/risk/test_precedence.py"
Task: "Write tests/integration/risk/test_order_cap_race.py"
# then, once they fail for the right reason:
Task: "Add the buy-side stops to src/trading_agent/risk/gate.py"
```

---

## Implementation Strategy

### MVP (User Story 1)

Setup → Foundational → US1: decisions become sized, idempotent, recorded verdicts. **Stop and
validate** with the US1 tests and a manual `evaluate_decision` against the test database.

### Incremental Delivery

1. US1: sizing and recording
2. US2: every hard stop, and the race-proof order cap
3. US3: the daily-loss halt and baseline, persisted
4. US4: stop-loss exits through the gate
5. US5: config failure modes, proven end to end
6. Polish: the property tests prove SC-001 to SC-003 across ≥10,000 states each

## Notes

- A failing property test is a real counterexample, not flakiness. Hypothesis prints the shrunk
  minimal input; fix the gate, then add that input as an example test.
- Commit after each phase checkpoint.
- Never point `TEST_DATABASE_URL` at the Railway database.
