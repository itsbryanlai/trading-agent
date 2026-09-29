---

description: "Task list for the universe reference-data job (feature 004)"
---

# Tasks: Universe Reference-Data Job

**Input**: Design documents from `/specs/004-reference-data/`

**Prerequisites**: plan.md, spec.md (with Clarifications), research.md (D1–D14), data-model.md,
contracts/market-data-port.md, contracts/reference-data-interface.md, quickstart.md,
[ADR 0010](../../docs/adr/0010-stop-loss-monitor-and-universe-reference-data.md) §3,
[ADR 0013](../../docs/adr/0013-deterministic-services-run-their-own-loops.md). The shared grants
contract is `specs/001-data-model/contracts/role-grants.md`, amended for this feature.

**Tests**: Included, and written first within each story. The spec requires no test to touch the
provider (FR-026). SC-004, SC-005 and SC-007 are universal claims, backed by Hypothesis tests and
the both-ways grants test.

**Organization**: One phase per user story (US1–US4 in spec.md). The pure core (`normalize`,
`symbols`, `schedule`) is built in US1. `service.tick()` is built in US1 and hardened in US2
(failures), US3 (intraday pick-up) and US4 (reporting).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4

## Conventions every task follows

- **No task ever contacts Finnhub or the broker.** Tests use `tests/fakes/market_data.py`, or the
  adapter with an injected opener that returns canned responses. The suite-wide guard in
  `tests/conftest.py` fails any connection that isn't local. The live `--check` is run only by the
  owner (quickstart step 4).
- **Never read, print or grep for real credentials or `.env` files.** Tests use
  `monkeypatch.setenv` with obviously fake values, such as `test-key-not-real`.
- **Money, prices, volumes and market caps are `decimal.Decimal`**, never `float`. Provider
  numbers become `Decimal` at the adapter boundary. Non-finite or unparseable values become
  `None`.
- **The pure core** (`reference/normalize.py`, `symbols.py`, `schedule.py`) never imports
  `psycopg`, `urllib`, `os` or `reference/service.py`, and never reads the clock; `now` is always
  an argument. `schedule.py` and `symbols.py` may import `trading_agent.risk.calendar`.
- **Failure reasons** are referenced only through constants in `reference/normalize.py`. Their
  strings match the table in `contracts/reference-data-interface.md` exactly.
- **The service takes** a `psycopg.Connection` (autocommit, connected as or `SET ROLE` to
  `ta_reference_data`), a `MarketDataProvider`, a loaded config, `now: datetime`, and injected
  `sleep` and `monotonic` callables so pacing is testable without real waiting.
- **Test clock**: Monday **2026-09-28 12:30 UTC (08:30 ET)**, which is after the 08:00 ET start
  and before the 13:30 UTC open, unless the test is about another time. Other times:
  - Before the window: 11:30 UTC (07:30 ET).
  - During the session: 14:00 UTC.
  - Close: 20:00 UTC.
  - Weekend: 2026-09-26. Holiday: 2026-11-26.
  - Early close: 2026-11-27 (close at 18:00 UTC).
  
  These are the same as features 002 and 003.
- **Test connections**: integration tests use feature 001's rolled-back `conn` fixture (outer
  transaction opened first) and `as_role`, and pass `_allow_savepoints=True` where the service
  requires autocommit, as in 003.
- **Mutation-check every new test**: break the code on purpose, confirm the test fails, then
  revert with a direct edit.
- A committed migration is never edited; `0009` is new.

---

## Phase 1: Setup

- [X] T001 [P] Create packages: `src/trading_agent/reference/__init__.py` (a docstring naming ADR 0010 §3 and ADR 0013), `tests/unit/reference/__init__.py`, `tests/integration/reference/__init__.py`
- [X] T002 [P] Add to `.env.example`, names and comments only with no values, a `--- Reference-data job (specs/004-reference-data) ---` section:
  - `REFERENCE_DATA_FINNHUB_API_KEY`: a read-only Finnhub key for this job only. It can't trade. If it shares a Finnhub account with Research later, the two share its rate limit (D13).
  - `REFERENCE_DATA_DATABASE_URL`: a login in `ta_reference_data`.
- [X] T003 [P] Create `config/reference_data.yaml` with a header comment. The comment names `contracts/reference-data-interface.md` and says the file is changed only through code review (FR-002) and that no agent writes it. Contents: `seed_symbols: []`, and `calls_per_minute: 30` with a comment that 30 is half the commonly quoted free limit, which is unconfirmed (D8)

---

## Phase 2: Foundational (blocks every story)

- [X] T004 Write the failing test `tests/integration/storage/test_reference_view.py` for migration 0009 (D10, data-model.md), as `as_role("ta_reference_data")`. It must check:
  - `SELECT` on `reference_candidate_symbols` returns exactly the columns `symbol`, `source`, `named_at`, `active_until`;
  - a held position appears with `source='position'`, with `named_at` and `active_until` null;
  - a report 3 days old appears with `source='report'`, its latest `generated_at` and latest `expires_at`;
  - a report 30 days old that is still active (`expires_at` in the future) appears, and so does an old expired one: the view has no time filter (D10, analyze F2–F3); the window is applied in code;
  - a `no_action` report (symbol null) does not appear;
  - a decision appears with `source='decision'`;
  - `SELECT` on `positions`, `reports` and `decisions` directly raises `InsufficientPrivilege`;
  - `UPDATE instrument_reference` raises `InsufficientPrivilege`;
  - `INSERT` works;
  - `INSERT … ON CONFLICT (symbol, trading_day) DO NOTHING` leaves an existing row unchanged;
  - the `reports` policies in `pg_policies` are identical before and after 0009.
- [X] T005 Create `src/trading_agent/storage/migrations/0009_reference_data.sql`, headed with a comment citing `specs/004-reference-data` research D10. It must:
  - `REVOKE UPDATE ON instrument_reference FROM ta_reference_data`;
  - `CREATE VIEW reference_candidate_symbols` as a UNION of:
    - `positions` → (`symbol`, `'position'`, NULL, NULL);
    - `reports WHERE symbol IS NOT NULL`, grouped by symbol → (`symbol`, `'report'`, `max(generated_at)`, `max(expires_at)`);
    - `decisions`, grouped by symbol → (`symbol`, `'decision'`, `max(generated_at)`, NULL);
    - no time filter and no use of `now()` (D10);
  - not `security_invoker`;
  - `GRANT SELECT ON reference_candidate_symbols TO ta_reference_data, ta_assistant, ta_dashboard`.
  
  Make T004 pass.
- [X] T006 Amend `tests/integration/storage/grants_matrix.py`: `instrument_reference` → `ta_reference_data: {"S", "I"}`, and add `reference_candidate_symbols` for `ta_reference_data`, `ta_assistant` and `ta_dashboard`, each `{"S"}`. Then amend `specs/001-data-model/contracts/role-grants.md` to match: the matrix row for `instrument_reference` shows `S, I`, a new row for the view, and a footnote ³ "Amended by `specs/004-reference-data` (migration `0009`)" explaining the revoke and the view. Run `tests/integration/storage/test_grants.py` and confirm the both-ways check passes. Mutation-check it by temporarily leaving `U` in the matrix and confirming the test fails
- [X] T007 [P] Write the failing test `tests/unit/reference/test_config.py`, then create `src/trading_agent/reference/config.py` (D11, contract "Configuration"). `load_config(path) -> ReferenceConfig(seed_symbols: tuple[str, ...], calls_per_minute: int)`, raising `ReferenceConfigError` when:
  - a key is missing or an unknown key is present;
  - `seed_symbols` isn't a list of strings, or has duplicates, or has an entry that fails the ticker check `^[A-Z]{1,5}([.-][A-Z]{1,2})?$`;
  - `calls_per_minute` isn't an int in 1–300 (`bool` rejected).
  
  `DEFAULT_CONFIG_PATH` points at the repo's `config/reference_data.yaml`. Test that the shipped file loads
- [X] T008 [P] Create `src/trading_agent/reference/provider.py` per `contracts/market-data-port.md`:
  - frozen dataclasses `Listing(symbol, type, mic)`, `Profile(symbol, market_cap_millions)`, `Quote(symbol, previous_close)`, `Metrics(symbol, avg_volume_10d_millions)`, where numeric fields are `Decimal | None`;
  - exceptions `KeyRejected`, `RateLimited`, `ProviderUnavailable`, all subclasses of `ProviderError`;
  - `class MarketDataProvider(Protocol)` with `list_us_symbols() -> dict[str, Listing]`, `get_profile(symbol)`, `get_quote(symbol)`, `get_metrics(symbol)`.
- [X] T009 [P] Create `tests/fakes/market_data.py`, a `FakeMarketData` implementing the protocol, per the contract's "What the fake must model":
  - scripted listings, profiles, quotes and metrics per symbol;
  - `fail(call, symbol=None, error=..., after=0)` to raise any `ProviderError` from any call, including after N successes;
  - a `calls` list of `(monotonic_time, call, symbol)` using an injected clock.
  
  Add `tests/unit/reference/test_fake_market_data.py` covering the scripting and the failure injection
- [X] T010 [P] Write the failing test `tests/unit/reference/test_import_guard.py` (D14, FR-020). Walking every module under `trading_agent.reference`, it asserts that none imports `alpaca`, `trading_agent.execution` or `trading_agent.risk.service`/`gate`/`rules`, and that no module's source mentions `ALPACA_`, `EXECUTION_DATABASE_URL`, `RISK_GATE_DATABASE_URL` or `ADMIN_DATABASE_URL`. It also asserts that the pure-core modules (`normalize`, `symbols`, `schedule`) don't import `psycopg`, `urllib` or `os`. It passes vacuously now and guards every later task.

- [X] T011 [P] Add `previous_session(day: date) -> date` to `src/trading_agent/risk/calendar.py` (the XNYS session before `day`, whether or not `day` is itself a session), with tests in `tests/unit/risk/test_calendar.py`: Monday 2026-09-28 → Friday 2026-09-25; Friday 2026-11-27 → Wednesday 2026-11-25 (Thanksgiving skipped); Saturday 2026-09-26 → 2026-09-25. Used for the FR-001 window (D10, analyze F9). No change to any existing function

**Checkpoint**: migration 0009 applied and grants verified both ways; config, port, fake and calendar helper ready.

---

## Phase 3: User Story 1 — Today's reference data is in place before the open (P1) 🎯 MVP

**Goal**: on a trading day from 08:00 ET, every candidate symbol with complete, sane provider data gets exactly one row for today.

**Independent test**: with the fake provider and a database holding positions, reports, decisions and seed symbols, one tick at 08:30 ET writes one normalized row per symbol for 2026-09-28, and a gate evaluation of a buy in one of them no longer returns `universe_no_reference_data`.

### Tests for User Story 1 (write first, confirm failing)

- [X] T012 [P] [US1] `tests/unit/reference/test_normalize.py`: table tests for `normalize(symbol, listing, profile, quote, metrics) -> ReferenceRow | Failure` (D2–D5).
  - **Type mapping**: `"Common Stock"`, `"common stock"` → `common_stock`; `"ETP"`, `"ETF"` → `etf`; `"ADR"` → `adr`; `"REIT"`, `"Preferred"`, `"Unit"`, `"Right"`, `"Warrant"`, `"Some New Type"` → `other`.
  - **Exchange mapping**: `XNGS`, `XNMS`, `XNCM`, `XNAS` → `XNAS`; `XNYS`, `XASE` unchanged; `ARCX`, `BATS`, `OTCM`, `OOTC` recorded unchanged.
  - **Units**: market cap `1415993` → `1415993000000`; volume `32.50147` × `pc` `150.25` → dollar volume `Decimal("32.50147") * 10**6 * Decimal("150.25")`; share price = `pc`.
  - **Rounding**: values are rounded `ROUND_HALF_EVEN` to 2 decimal places (market cap, dollar volume) and 4 (price).
  - **Failures**, one per reason in the contract table: `not_listed` (listing `None`), `missing_type`, `missing_mic`, `missing_market_cap` (None, 0, negative), `missing_price` (None, 0, negative), `missing_volume` (None, 0, negative), `implausible_market_cap` (market cap above $20 trillion; exactly $20 trillion is allowed), `implausible_dollar_volume` (dollar volume greater than market cap; equal is allowed), `value_out_of_range` (a `pc` of `0.00001` rounds to 0; a market cap or dollar volume too large for `numeric(20,2)`).
- [X] T013 [P] [US1] `tests/unit/reference/test_normalize_properties.py`, Hypothesis over arbitrary `Decimal | None` inputs and arbitrary type and MIC strings (SC-005). A `ReferenceRow` always has `security_type` in the four allowed values, `share_price_usd > 0`, `0 < market_cap_usd ≤ 20 trillion`, `0 < avg_daily_dollar_volume_usd ≤ market_cap_usd`, every value finite and within its column's precision after rounding. `security_type == "common_stock"` only when the type string, case-folded, equals `"common stock"`. Otherwise the result is a `Failure`. Check that the generator really reaches both results (for example with `hypothesis.event` or `target`).
- [X] T014 [P] [US1] `tests/unit/reference/test_symbols.py` for `build_symbol_set(candidates, seeds, now) -> list[str]` and `is_plausible_ticker` (FR-001, FR-003, D8).
  - **Accepted tickers**: `AAPL`, `BRK.B`, `BF-B`.
  - **Rejected tickers**: `aapl`, `AAPLXX`, `BRK.`, `BRK.BBB`, `A B`, `""`, `$AAPL`, `1234`.
  - **FR-001 window**, with the previous session's open from `calendar`, so on Monday 2026-09-28 that is Friday 2026-09-25 13:30 UTC:
    - a report with `active_until > now` qualifies even if it is old;
    - an expired report named before the previous open does not;
    - a decision at the previous open qualifies, and one a minute before does not;
    - positions always qualify;
    - seeds always qualify.
  - **Deduplication**: each symbol appears once.
  - **Order**: symbols from reports still active, or from decisions made today, first (D8), then positions, then other recent symbols, then seeds, alphabetical within each group, and deterministic.
  - **Implausible tickers**: returned separately as skipped, with reason `invalid_symbol`.
- [X] T015 [P] [US1] `tests/unit/reference/test_schedule.py` for `fetch_allowed(now) -> bool` (D7, FR-013, FR-015).
  - **False**: on weekend 2026-09-26, on holiday 2026-11-26, at 07:59:59 ET, and at or after the close (20:00 UTC; 18:00 UTC on 2026-11-27).
  - **True**: at 08:00:00 ET, at 08:30, at 14:00 UTC, and at 17:59 UTC on 2026-11-27.
  - Naive datetimes raise `ValueError`.
- [X] T016 [P] [US1] `tests/unit/reference/test_service_tick.py`, using the fake provider and an in-memory `ReferenceStore` (the service's database seam, defined in T021: `read_candidates()`, `recorded_symbols(day)`, `insert(row, day)`), so the unit test needs no Postgres.
  - One tick at 08:30 ET fetches `list_us_symbols` once and three calls per symbol.
  - Each row is inserted with `trading_day=2026-09-28`.
  - A second tick that day makes no calls for recorded symbols and doesn't fetch the US list again (FR-011, FR-018).
  - A tick at 07:30 ET or on a weekend makes no calls.
  - Pacing: with `calls_per_minute=30`, consecutive provider calls are ≥ 2.0 s apart on the injected `monotonic`, and a tick stops starting new symbols after about 50 s of work (D8).
  - SC-002: 200 candidate symbols at `calls_per_minute=30`, with ticks every 60 s on the injected clock from 08:00 ET, are all recorded before 09:15 ET.
- [X] T017 [US1] `tests/integration/reference/test_tick_pre_open.py`, against Postgres as `ta_reference_data` with the fake provider.
  - Seed 2 positions, 2 reports, 1 decision and 1 seed symbol, then run ticks at 08:30 ET until done.
  - Exactly one row per symbol for 2026-09-28, with the fake's normalized values.
  - No row for a symbol outside the set.
  - Re-running leaves rows byte-identical (FR-018).
  - Then, at 14:00 UTC the same day (the gate rejects `market_closed` before any other rule, so a pre-open evaluation proves nothing; analyze F1), as `ta_risk_gate`, `risk.service.evaluate_decision` for a buy in a qualifying symbol does not return `universe_no_reference_data` and passes the universe rules (US1 independent test).
  - Build fixtures with `tests/integration/storage/factories.py`.

### Implementation for User Story 1

- [X] T018 [P] [US1] Create `src/trading_agent/reference/normalize.py`: `ReferenceRow` (frozen: `symbol`, `security_type`, `exchange_mic`, `market_cap_usd`, `avg_daily_dollar_volume_usd`, `share_price_usd`), `Failure(symbol, reason)`, one reason constant per row of the contract's "Failure reasons" table, and `normalize(...)` per D2–D5. Make T012 and T013 pass
- [X] T019 [P] [US1] Create `src/trading_agent/reference/symbols.py`: `TICKER_PATTERN`, `is_plausible_ticker`, `Candidate(symbol, source, named_at, active_until)`, and `build_symbol_set(candidates, seeds, now) -> SymbolSet(ordered: list[str], skipped: list[Failure])`, using `calendar.previous_session` and `calendar.open_time` for the previous session's open. Share `TICKER_PATTERN` with `config.py`, which must import it from here. Make T014 pass
- [X] T020 [P] [US1] Create `src/trading_agent/reference/schedule.py`: `FETCH_START_ET = time(8, 0)`, and `fetch_allowed(now)` using `calendar.is_session`, `calendar.close_time` and America/New_York time. Make T015 pass
- [X] T021 [US1] Create `src/trading_agent/reference/service.py`. It defines `ReferenceStore` (a `Protocol`: `read_candidates() -> list[Candidate]`, `recorded_symbols(day) -> set[str]`, `insert(row, day) -> None` using `INSERT … ON CONFLICT (symbol, trading_day) DO NOTHING`) and `PgReferenceStore(conn, *, _allow_savepoints=False)`, which requires autocommit unless `_allow_savepoints` (raise `NotAutocommit`, as Execution does). `ReferenceJob(provider, store, config, *, sleep, monotonic, symbol_list=None)` has `tick(now) -> TickReport`:
  1. if `not fetch_allowed(now)`, return;
  2. fetch the US list if it isn't cached for today's trading day;
  3. read `reference_candidate_symbols`;
  4. read today's recorded symbols from `instrument_reference`;
  5. `build_symbol_set`;
  6. for each unrecorded symbol, in order, within the ~50 s budget, fetch profile, quote and metrics through the pacer, normalize, and on a `ReferenceRow` run `INSERT … ON CONFLICT (symbol, trading_day) DO NOTHING` with `trading_day=calendar.trading_day(now)`.
  
  `TickReport` counts `candidates`, `recorded`, `already`, `failed` and `deferred`. The cached US list is tagged with its trading day and refetched when the day changes (analyze F13). Make T016 and T017 pass

**Checkpoint**: the MVP. Buys in candidate symbols can pass the universe check.

---

## Phase 4: User Story 2 — A failed or implausible fetch fails closed for that symbol only (P1)

**Goal**: any provider failure, missing field or implausible value writes nothing for that symbol today, never carries a row forward, logs the failure, backs off and retries. Other symbols are unaffected. The key is checked at startup and handled once per run afterwards.

**Independent test**: with the fake failing chosen symbols (errors, `{}` fields, out-of-range values, 429, key rejection), those symbols have no row for today even when yesterday's exists, the others do, and a later tick after recovery fills them in.

### Tests for User Story 2 (write first, confirm failing)

- [X] T022 [P] [US2] `tests/unit/reference/test_service_failures.py`, with the fake provider:
  - **One symbol failing** `ProviderUnavailable` on `get_quote`: no insert for it, the others are inserted, `failed=1`.
  - **Backoff**: the next attempts come at +5, +10, +20, then every +30 minutes, and a tick before the next attempt makes no calls for it (FR-014, D8).
  - **Recovery**: after the provider recovers, the next due tick records it.
  - **Backoff applies to every per-symbol reason** except `rate_limited` (D8, analyze F7): a `not_listed` symbol and an `implausible_dollar_volume` symbol get no calls and no new WARNING until their next attempt; an `invalid_symbol` is logged once per attempt, not every tick.
  - **Database error on one insert**: the store raises `psycopg.errors.CheckViolation` for one symbol → counted `failed` with `database_error`, backed off, other symbols still inserted, no exception out of `tick`; the store raising `psycopg.OperationalError` propagates (D9, analyze F6).
  - **`RateLimited`**: the tick stops starting new symbols, the symbol isn't counted toward backoff, and the next tick continues (FR-016).
  - **`KeyRejected` mid-run**: the rest of the tick is skipped, exactly one ERROR log line is written in the contract's format, and no provider call is made until 15 minutes later (FR-019a).
  - **`ProviderUnavailable` on `list_us_symbols`**: no symbol is fetched, and the list is retried next tick.
- [X] T023 [P] [US2] `tests/unit/reference/test_finnhub_adapter.py` for `FinnhubProvider(api_key, *, opener=None, timeout=10)`, with an injected opener that returns canned responses (D6). Canned bodies copy the swagger samples' field names: `{"c":…, "pc":…}`, `{"marketCapitalization":…}`, `{"metric":{"10DayAverageTradingVolume":…}}`, and a list of `{"symbol","type","mic",…}`.
  - Every request carries `X-Finnhub-Token` and no URL contains the key.
  - 401 and 403 → `KeyRejected`; 429 → `RateLimited`; 500, a timeout, `URLError` and bad JSON → `ProviderUnavailable`.
  - A `{}` body → the value's fields are `None`.
  - `"NaN"`, `"inf"` and non-numeric values → `None`.
  - `repr(provider)` and the str of every raised exception don't contain the key.
  - Endpoint paths and query parameters exactly as D2 lists them.
- [X] T024 [US2] `tests/integration/reference/test_fail_closed.py`, against Postgres with the fake provider (US2 independent test, SC-004, SC-006).
  - A symbol with yesterday's (2026-09-25) row and a failing provider today: no 2026-09-28 row, and the gate's buy evaluation at 14:00 UTC (in session; analyze F1) returns `universe_no_reference_data`.
  - An implausible dollar volume: no row.
  - Under a total provider outage no row at all is written, and, as `ta_risk_gate` at 14:00 UTC, a stop-loss trigger evaluation (trigger observed within the last 10 minutes) for a held symbol with no reference data is still approved as an exit.
  - Assert that no row is ever written for any `trading_day` other than today's, across all scenarios.

### Implementation for User Story 2

- [X] T025 [P] [US2] Create `src/trading_agent/reference/finnhub.py`: `FinnhubProvider`, fixed base URL `https://finnhub.io/api/v1`, `urllib.request` with an injectable opener, no retries, status mapping and `Decimal` parsing at the boundary, and a `__repr__` without the key. Make T023 pass
- [X] T026 [US2] Extend `service.py` with:
  - per-symbol backoff state (5, 10, 20, then 30 minutes);
  - `RateLimited` stops the tick;
  - `KeyRejected` skips the tick and sets a 15-minute key backoff;
  - one ERROR line per key-rejected tick;
  - `ProviderUnavailable` on the list call;
  - backoff for every per-symbol reason except `rate_limited`;
  - a non-`OperationalError` `psycopg.Error` from `store.insert` → `database_error`.
  
  Make T022 and T024 pass

**Checkpoint**: every failure path withholds the row and leaves the rest of the job running.

---

## Phase 5: User Story 3 — A symbol named during the day gets data the same day (P2)

**Goal**: a symbol named by a report or decision during the session is recorded within 5 minutes (SC-003). Nothing is fetched after the close.

**Independent test**: after the morning ticks, insert a report naming a new symbol at 14:00 UTC. The next tick records it, previously recorded symbols get no calls, and after 20:00 UTC a newly named symbol is not fetched.

- [X] T027 [P] [US3] `tests/integration/reference/test_intraday_pickup.py`:
  - **Pick-up**: after the 08:30 ET ticks, insert a report for `NEWCO` at 14:00 UTC; the tick at 14:01 UTC records `NEWCO` and makes no calls for any other symbol.
  - **Mid-run pick-up** (D8): during a long morning run of 30 symbols (the fake's clock advances per call), a report inserted mid-run is recorded within the next two ticks, ahead of the lower-priority symbols not yet fetched.
  - **After the close**: at 20:00 UTC a newly named symbol gets no calls, and at 18:00 UTC on 2026-11-27 (an early close) likewise.
- [X] T028 [US3] Adjust `service.py` if T027 exposes a gap. The tick already rebuilds the set each time (T021), so this is expected to pass with no code change. If so, record that in the task's implementation note rather than inventing a change

**Checkpoint**: intraday ideas become buyable the same day.

---

## Phase 6: User Story 4 — The owner can see how the day's run went (P3)

**Goal**: log lines that make a missing-data rejection explainable, in the formats given in `contracts/reference-data-interface.md` "Log lines".

**Independent test**: a tick with a mix of recorded and failing symbols writes one INFO summary and one WARNING per failed or skipped symbol. The first tick at or after the open names every candidate still missing.

- [X] T029 [P] [US4] `tests/unit/reference/test_logging.py`, with `caplog`:
  - the INFO summary shows `day=`, `candidates=`, `recorded=`, `already=`, `failed=` and `deferred=`;
  - one WARNING per failed attempt (not per tick), showing the symbol, its reason and its next attempt time;
  - `invalid_symbol` skips are logged;
  - an open warning at the first tick at or after 13:30 UTC names the missing symbols, and is not repeated at the next tick;
  - no open warning on a day where all are recorded;
  - across every log record in the file's tests, neither the fake key string nor a fake database URL appears (FR-023).
- [X] T030 [US4] Add the log lines and the once-per-day open warning (`schedule.open_warning_due(now, already_warned_day)`, pure, with a test added to `test_schedule.py`) to `service.py` and `schedule.py`. Make T029 pass

---

## Phase 7: The process (`python -m trading_agent.reference`)

Serves all stories (FR-012, FR-017, FR-019, FR-019a, FR-020, D12, D13).

- [X] T031 [P] `tests/unit/reference/test_main.py`, mirroring `tests/unit/execution/test_runner.py`, with injected `provider_factory`, `connect`, `job_factory`, `sleep`, `clock` and `max_ticks`. Exit codes per the contract:
  - **Exit 2**: a missing `REFERENCE_DATA_FINNHUB_API_KEY` or `REFERENCE_DATA_DATABASE_URL` (the message names the variable, not a value); a bad config file; `KeyRejected` from the startup `list_us_symbols`; `ProviderUnavailable` from the startup check; another instance holding the lock after the wait.
  - **Exit 3**: a connect `OperationalError`; `OperationalError` from a tick; the connection closing.
  - **Exit 0**: `max_ticks` reached.
  - **Provider errors inside a tick** don't exit.
  - **No other variables**: `os.environ` is read only for the two variable names (patch `os.environ` with a recording mapping).
- [X] T032 [P] `tests/unit/reference/test_check_mode.py` for `--check AAPL BRK.B bad$`:
  - needs only the key; `connect` is never called;
  - prints one line per symbol, with the row's values or `failed: <reason>`, and `invalid_symbol` for `bad$`;
  - writes nothing, returns 0, and returns 2 on `KeyRejected`.
- [X] T033 Create `src/trading_agent/reference/__main__.py`:
  - `main(argv=None, *, provider_factory=FinnhubProvider, connect=psycopg.connect, job_factory=ReferenceJob, sleep=time.sleep, clock=..., max_ticks=None) -> int`;
  - `TICK_SECONDS = 60`;
  - startup order per D13: env, then config, then the key check via `list_us_symbols`, then connect (autocommit, `dict_row`, keepalives as Execution does), then the server-side keepalive `SET`s, then a single-instance `pg_try_advisory_lock(0x72656631)` ("ref1"; distinct from Execution's single-instance `0x65786531`, its transaction lock `0x65786563`, and the gate's `0x7269736B`; analyze F8), with the same wait and retry as `Executor.startup`. The startup symbol list is passed to the job tagged with `calendar.trading_day(startup_now)` (F13);
  - the `--check` path;
  - `logging.basicConfig` under `__main__`.
  
  Make T031 and T032 pass
- [X] T034 `tests/integration/reference/test_single_instance.py`: two jobs on two connections; the second can't take the lock while the first holds it, and takes it after the first connection closes (FR-017)

---

## Phase 8: Polish & cross-cutting

- [X] T035 [P] Write `docs/specs/reference-data.md`, the behavior spec for the new component (Constitution V; `docs/specs/README.md` "What goes in a spec"): purpose and non-goals, inputs (the candidate view, seed config, provider), output (`instrument_reference`), edge cases, interfaces (the gate reads; the Assistant and dashboard read), and failure behavior, citing ADR 0010 §3, ADR 0013 and `specs/004-reference-data`. Note that the job fetches intraday as well as before the open, beyond ADR 0010's "once per trading day"; its own cadence is covered by ADR 0013 (analyze F12). Add it to the index in `docs/specs/README.md`
- [X] T036 [P] Update `docs/specs/data-model.md`'s `instrument_reference` section (the writer is insert-only after 0009) and add `reference_candidate_symbols`, citing `specs/004-reference-data` research D10. Update `docs/specs/risk-gate.md` only if it names the writer's permissions
- [X] T037 [P] Update `docs/architecture/overview.md` to show the reference-data job as its own process beside Execution and the gate's runner (ADR 0013), feeding `instrument_reference` to the gate, referencing ADR 0010 §3
- [X] T038 [P] Mutation-check pass over the new tests. Covered code:
  - the D3 and D4 mappings;
  - the plausibility checks (dollar volume ≤ market cap, the $20 trillion ceiling, column bounds);
  - the `ON CONFLICT DO NOTHING`;
  - the FR-001 window boundary;
  - the backoff schedule;
  - the key-backoff;
  - the header-only key;
  - the 08:00 and close boundaries.
  
  For each: break it on purpose, confirm a test fails, then revert with a direct edit. Record the results in the implementation notes below
- [X] T039 Run the full offline suite, the integration suite and lint per `quickstart.md` §1–3. All must pass (expect 271+ offline, 1033+ integration). Fix anything that fails
- [X] T040 Walk through `quickstart.md` and confirm every command and expected outcome matches the implementation, except step 4 (the owner-run live `--check`), which is left for the owner

---

## Dependencies & execution order

- **Setup (T001–T003)**: no dependencies.
- **Foundational (T004–T011)**: T005 after T004; T006 after T005; T007–T011 are parallel with each other and with T004–T006. They block all stories.
- **US1 (T012–T021)**: tests T012–T016 in parallel; T017 after T005; T018–T020 in parallel; T021 after T018–T020, T008–T009 and T011.
- **US2 (T022–T026)**: after T021. T025 is independent of T026.
- **US3 (T027–T028)**: after T021. Independent of US2 in code, but run it after US2 so backoff is in place.
- **US4 (T029–T030)**: after T026.
- **Process (T031–T034)**: T033 after T021, T025 and T026.
- **Polish (T035–T040)**: T035–T037 any time after Foundational; T038–T040 last.

### Parallel examples

- Foundational: T007, T008, T009, T010 and T011 together, while T004 → T005 → T006 run in sequence.
- US1 tests: T012, T013, T014, T015 and T016 together; then T018, T019 and T020 together.
- US2: T023 → T025 alongside T022 → T026.

## Implementation strategy

1. **MVP**: Setup, Foundational and US1. Buys become possible in candidate symbols, with fail-closed normalization already in place (T018 includes every sanity check).
2. **US2**: failure isolation, backoff, key handling and the real adapter. The job is not deployable before this.
3. **US3, then US4, then the process**: intraday pick-up, reporting, then the runnable loop.
4. **Polish**: docs, mutation pass, full suites.
5. **Owner**: quickstart step 4 (the live `--check`) before any deployment.

## Implementation notes

- **Test counts**: 460 offline (was 271), 1067 integration (was 1033). Lint and format clean.
  The one warning is a pre-existing `websockets.legacy` deprecation from alpaca-py's dependencies.
- **Order of work (T021 vs T026/T030)**: `service.py` was written in one pass in T021. It already
  included the US2 failure handling and backoff and the US4 log lines, so the US2 and US4 tests
  (T022, T024, T029) passed when first run instead of failing first. Their value was established by
  the mutation pass (T038) instead: every behaviour they cover was broken on purpose and caught.
- **T027/T028 (US3)**: no code change was needed. The tick rebuilds the candidate set every time
  and puts symbols named today first (T018/T021), so the intraday pick-up tests passed as the task
  predicted.
- **Test helpers**: `tests/unit/reference/support.py` holds the fake clock (`sleep` advances it,
  and each provider call can cost time) and the in-memory `ReferenceStore`. Both are reused by the
  integration `Harness` in `tests/integration/reference/conftest.py`.
- **`--check` output**: each line also prints the provider's raw type and MIC strings, so the owner
  can see what the D3/D4 mappings received (quickstart step 4).
- **Main loop timing**: ticks start every 60 s. A tick that runs longer (up to about 50 s of
  fetching, plus a final call) is followed straight away by the next, which is the model the SC-002
  test uses.
- **T038 mutation pass**: 25 mutations, each applied, tested and restored by a scratch script that
  rewrote the file's saved text (no git restore). One survived at first: removing
  `ON CONFLICT DO NOTHING`. It went unnoticed because the job never inserts a symbol it has already
  recorded. `test_store_insert_is_idempotent_when_the_row_already_exists` was added, and the
  mutation is now caught. The other 24 mutations were caught at once:
  - the D3 type mapping, the Nasdaq tiers, and unmapped exchange codes;
  - the checks for dollar volume ≤ market cap, the $20T ceiling, column bounds, and zero volume;
  - the FR-001 window boundary and `previous_session`;
  - the backoff schedule, backoff after normalize failures, the key backoff, and no backoff on a
    rate limit;
  - the isolation of database errors, and the header-only key;
  - the 08:00 start, the close boundary, the tick budget, and pacing;
  - the day tag on the startup symbol list, the startup key check, and the open warning appearing
    once;
  - the check on seed tickers, and the REVOKE in 0009.
- **Guard hook**: a mutation script that copied the process environment was blocked by the local
  credential guard. The script was rewritten without the copy (subprocesses inherit the environment
  anyway), not routed around the hook.
- **Not done here, as planned**:
  - removing `ta_risk_gate`'s unused SELECT on `system_state_effective`;
  - deployment config;
  - the owner-run live `--check` (quickstart step 4), which is required before deploying.
