---

description: "Task list for the Opportunistic Identifier agent (feature 011)"
---

# Tasks: Opportunistic Identifier agent

**Input**: Design documents from `/specs/011-opportunistic-identifier/`

**Prerequisites**:
- plan.md, spec.md (with Clarifications), research.md (O1–O15), data-model.md, quickstart.md;
- contracts/oi-interface.md and contracts/ports.md;
- ADRs [0002](../../docs/adr/0002-pm-synthesizes-rather-than-analysts-deciding.md), [0011](../../docs/adr/0011-event-driven-portfolio-manager-runs.md), [0015](../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md), [0016](../../docs/adr/0016-market-data-for-the-llm-agents.md), [0017](../../docs/adr/0017-original-analysts-skip-incubation.md), [0018](../../docs/adr/0018-qwen-as-a-model-provider.md).

**Tests**: included and written first within each story (Constitution: every logic change is covered by tests). SC-002 (nothing off the shortlist is written) and SC-003 (even coverage) are universal claims, backed by Hypothesis properties of pure modules.

**Organization**: one phase per user story in spec.md.
- **US1**: the happy path end to end, plus FR-023 (a `no_action` row doesn't wake the PM).
- **US2**: every drop rule and the SC-002 property.
- **US3**: every failure path, the window and the deadline.
- **US4**: `--dry-run`, `--check` and token logging.
- **Phase 7**: wiring for a disabled rollout: login, schedule, deployment and docs.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4

## Conventions every task follows

- **No task reaches the network or calls a model.** Unit tests use `tests/fakes/oi_market_data.py` (new) and `tests/fakes/model.py` (existing). The Finnhub adapter is tested with a fake `opener`, as `tests/unit/reference/test_finnhub.py` does. The suite-wide network guard in `tests/conftest.py` stays as it is.
- **Never read, print or grep for real credentials or `.env` files.** Tests set obviously fake values with `monkeypatch.setenv`, such as `OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY=fake-not-real`.
- **Pure modules** (`rotation.py`, `screen.py`, `prompt.py`, `answer.py`) never import `psycopg`, `urllib`, `anthropic`, `os` or `service`, and never read the clock: `now` and `today` are arguments.
- **Imports**: only what research O1 lists. Never `research`, `portfolio_manager`, `orchestrator` or `execution`.
- **Times** come from `trading_agent.risk.calendar`.
- **Test clock**: **Thursday 2026-10-08, 11:00 ET = 15:00 UTC** (EDT), unless the test is about another day.

  | Case | Date and time | Detail |
  |---|---|---|
  | Default | Thu 2026-10-08, 11:00 ET | slot 1 |
  | Window edges | 10:00 ET; 15:00 ET; 15:59 ET | slots 0, 5, 5 |
  | Early-close slots | Fri 2026-11-27 | 3 slots: 10:00, 11:00, 12:00 (cap 12:30) |
  | Before the first slot | 09:45 ET | |
  | Weekend | Sat 2026-10-10 | |
  | Holiday | Thu 2026-11-26 | |
  | Early close | Fri 2026-11-27 | EST, close 13:00 ET = 18:00 UTC |
- **Test connections**: integration tests use feature 001's rolled-back `conn` fixture (`tests/integration/conftest.py`) and `as_role` (`tests/integration/helpers.py`). Stores that need autocommit take `_allow_savepoints=True` in tests, like `PgResearchStore`.
- **Mutation-check every new test**: break the code on purpose, confirm a test fails, then restore the file's saved text with a direct edit, never `git checkout`.
- **Don't edit committed migrations.** `0014` is new.
- **Module size**: no module over 600 lines (`scripts/check_module_size.py`). If `service.py` nears 450, move the fetch loop to `fetch.py`.
- **Quality gate**: run `scripts/lint.sh` and the touched tests before each story's last task is ticked. No `# noqa`, and no raised limits.
- **Commits**: one per task or tight task group, in the style `Opportunistic Identifier (feature 011): what changed (Tnnn)`, staged by explicit path. Never stage `src/trading_agent/risk/__main__.py` or `job_*.csv`, which belong to the owner.
- **Risk Gate code (`src/trading_agent/risk/gate.py`, `rules.py`, `config.py`) is read and imported, never edited.**
- **Copy, don't import, from sibling agents.** Where a task says "as in `research/…`", copy the pattern into this package: the layering forbids importing `research` or `portfolio_manager`.

---

## Phase 1: Setup

- [x] T001 [P] Create the packages:
  - `src/trading_agent/opportunistic_identifier/__init__.py`, with a docstring naming ADRs 0002, 0016, 0017 and 0018 and saying "writes only its own buy reports; no portfolio, decisions or broker";
  - `tests/unit/opportunistic_identifier/__init__.py` and `tests/integration/opportunistic_identifier/__init__.py`.
- [x] T002 [P] Add `"opportunistic_identifier"` to the top layer of `[tool.importlinter]` in `pyproject.toml`, so it reads `"execution | orchestrator | research | portfolio_manager | opportunistic_identifier"`. Run `scripts/lint.sh` to confirm the contract still passes.
- [x] T003 [P] Create `config/opportunistic_identifier.yaml` exactly as in contracts/oi-interface.md "Configuration", with `scan_universe: []`, `slice_size: 40` and `finnhub_calls_per_minute: 20`. Header comment:
  - the schema contract;
  - changed only through code review, and no agent writes it;
  - `slots` must match `config/schedule.yaml`;
  - 20 calls a minute because the Finnhub account is shared (research O11);
  - to switch to Sonnet, set `provider: anthropic` and `name: claude-sonnet-5-5`, and set `OPPORTUNISTIC_IDENTIFIER_ANTHROPIC_API_KEY`.
- [x] T004 [P] Add a `--- Opportunistic Identifier (specs/011-opportunistic-identifier, ADR 0015/0016/0018) ---` section to `.env.example`, with names and comments only, no values:
  - `OPPORTUNISTIC_IDENTIFIER_DATABASE_URL`: a login in `ta_opportunistic_identifier`;
  - `OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY`: read-only. It shares the Finnhub account, so the OI paces at 20 calls a minute;
  - `OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY` and `OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL` (`https://` only): when `model.provider: qwen`;
  - `OPPORTUNISTIC_IDENTIFIER_ANTHROPIC_API_KEY`: only when `model.provider: anthropic`.

  All are set on the orchestrator's service. Do not quote any provider's plan terms.

---

## Phase 2: Foundational (blocks every story)

- [x] T005 [P] `tests/fakes/oi_market_data.py`: `FakeOIMarketData`, modeled on `tests/fakes/market_data.py`. It scripts listings, quotes, profiles and fundamentals per symbol, injects any `reference.provider` error from any call (optionally after N successful calls), and records `calls` as `(method, symbol)` so tests can assert call order and that a stale quote skips the other two calls.
- [x] T006 [P] Tests first, in `tests/unit/opportunistic_identifier/test_finnhub.py`, using a fake opener:
  - `/stock/symbol` is requested three times (`mic` XNYS, XNAS, XASE) and merged, with `description` kept and cut to 100 characters. Conflicting duplicates give `conflicting=True`;
  - `/quote`, `/stock/profile2` and `/stock/metric?metric=all` map to `Quote`, `CompanyProfile` (with `finnhubIndustry` cut to 100 characters) and `Fundamentals`, for every key in data-model.md "Fundamentals sent to the model";
  - zero, booleans, non-finite values and wrong types become `None`;
  - 401 → `KeyRejected`; 403 on the symbol list → `KeyRejected`; 403 per symbol → `NotPermitted`; 429 → `RateLimited`; other HTTP errors, timeouts, truncated bodies and non-JSON → `ProviderUnavailable`;
  - the key is in the `X-Finnhub-Token` header and in no URL, `repr`, `str` or exception message;
  - a redirect is refused.
- [x] T007 Implement `src/trading_agent/opportunistic_identifier/ports.py` (the `MarketData` protocol; `Listing` with `to_reference()`; `CompanyProfile` with `to_reference()`; `Fundamentals` with `to_metrics()`; re-export the four `reference.provider` errors) and `finnhub.py` (`OIFinnhub`, built like `reference/finnhub.py`, `BASE_URL = "https://finnhub.io/api/v1"`, `TIMEOUT_SECONDS = 10` (the per-call socket timeout the budget counts), using `open_without_redirects()`), so T006 passes. Add a test that `to_reference()` and `to_metrics()` give exactly the `reference.provider` types `reference.normalize.normalize` accepts. Also a test that the module's `SYMBOL_LIST_MICS == tuple(sorted(risk.rules.US_LISTED_MICS))`. **Adapter equivalence** (`/speckit-analyze` G1): feed identical fake Finnhub response bodies for `/stock/symbol`, `/stock/profile2`, `/quote` and `/stock/metric` through both `reference.finnhub.FinnhubProvider` and `OIFinnhub`, and assert `reference.normalize.normalize` returns the same `ReferenceRow` or `Failure` for each, including a conflicting listing, a non-USD market cap and a zero volume.
- [x] T007a Schedule timeout (research O10): in `config/schedule.yaml` set `opportunistic_identifier.timeout_minutes: 15`, with a comment citing research O10 (leave `env` and `enabled` for T040). Update the orchestrator tests that load the real schedule and assume 10 minutes:
  - `tests/unit/orchestrator/test_planner_timeouts.py` `test_a_run_past_its_timeout_is_stopped`: the Stop is at `et("11:15")`, and none at one second before;
  - `tests/unit/orchestrator/test_service_day.py` (around line 138): tick until after `et("11:20")`, and `finished_at - et("11:15") < timedelta(minutes=1)`;
  - `tests/unit/orchestrator/test_logging.py` (around line 60): the comment says "past its 15 minutes", and the tick moves to `et("10:16")` so the run is genuinely past its timeout.

  Run `pytest tests/unit/orchestrator tests/unit/deploy`. **Never revert the timeout to make a test pass**: the OI's run budget depends on it.
- [x] T008 Tests first, in `tests/unit/opportunistic_identifier/test_config.py`, for `config.load_config(path, risk_path)`:
  - every key is required and unknown keys are rejected, including in `slots` and `model` (via `llm.settings.parse_model_settings` with `timeout_bounds=(30, 300)`);
  - bounds: `slice_size` 1–200, `shortlist_size` 1–40 and ≤ `slice_size`, `quote_max_age_minutes` 1–60, `finnhub_calls_per_minute` 1–60, `rationale_max_chars` 200–10000, `max_input_chars` 5000–300000, `slots.every_minutes` 15–240, `slots.before_close_minutes` 0–120, and `slots.first` and `slots.last` as `HH:MM` with `first ≤ last`;
  - `scan_universe`: each entry passes `reference.symbols.is_plausible_ticker` and contains no `.` or `-`, with at most 1000 entries. Duplicates are de-duplicated, and the result is sorted;
  - the budget check (research O10), for the configured provider: `(3 + 3 × slice_size) × 60 / finnhub_calls_per_minute ≤ fetch_window`, where `fetch_window = RUN_BUDGET_SECONDS − RUN_MARGIN_SECONDS (60) − RUN_SLACK_SECONDS (60) − MODEL_ATTEMPTS[provider] × model.timeout_seconds − FINNHUB_CALL_TIMEOUT_SECONDS (10)` and `MODEL_ATTEMPTS = {"qwen": 1, "anthropic": 2}`. The shipped file passes (369 s ≤ 650 s). At 20 a minute with a 120 s model timeout: `slice_size: 71` passes and `72` fails on Qwen; `57` passes and `58` fails on Anthropic;
  - `MODEL_ATTEMPTS["anthropic"] == llm.anthropic_client.MAX_RETRIES + 1` (the test may import that module; `config.py` must not), and `FINNHUB_CALL_TIMEOUT_SECONDS == opportunistic_identifier.finnhub.TIMEOUT_SECONDS`;
  - `config/risk.yaml` is loaded with `risk.config.load_config`, and only its `universe` is kept;
  - the shipped `config/opportunistic_identifier.yaml` loads;
  - **the schedule guard**: `RUN_BUDGET_SECONDS (900) == 60 × config/schedule.yaml["opportunistic_identifier"]["timeout_minutes"]`, `slots.first == window_start`, `slots.last == window_end`, `slots.every_minutes == interval_minutes`, and `slots.before_close_minutes == portfolio_manager.before_close_minutes`. Read the YAML directly; never import `orchestrator`;
  - `fetch_deadline(start)` returns `start + fetch_window` seconds.
- [x] T009 Implement `src/trading_agent/opportunistic_identifier/config.py` (`OIConfig`, `Slots`, `OIConfigError`, `RUN_BUDGET_SECONDS = 900`, `RUN_MARGIN_SECONDS`, `RUN_SLACK_SECONDS`, `FINNHUB_CALL_TIMEOUT_SECONDS`, `MODEL_ATTEMPTS`, `OIConfig.fetch_window_seconds`, `OIConfig.fetch_deadline(start)`, `load_config`), so T008 passes. Error messages name the key, never echo a credential.
- [x] T010 [P] Tests first, in `tests/unit/opportunistic_identifier/test_rotation.py`:
  - `day_slots(day, slots)`: the times from `first`, every `every_minutes`, up to `min(last, close − before_close_minutes)`, exactly the orchestrator's `oi_slots` rule. A normal day gives 6 (10:00–15:00); 2026-11-27 (close 13:00) gives 3 (10:00, 11:00, 12:00); a non-session day gives none. An extra test compares `day_slots` with `orchestrator.planner.oi_slots` times for 20 sample days, including early closes (tests may import `orchestrator`; the package may not);
  - `slot_number(now, slots)`: the index of the latest of today's slots at or before `now`; 09:45 → 0; 15:59 → 5;
  - **a non-session day** (only reachable by `--dry-run`, since a real run stops at the window check): `slice_for` uses the next session, slot 0, and says so in the dry run's `slice` line. Test with Sat 2026-10-10 (→ Mon 2026-10-12, slot 0);
  - `run_index(today, now, slots)`: the number of slots on every session from 2026-01-02 up to the day before `today`, plus `slot_number`. Consecutive slots, across weekends, holidays and early closes, get consecutive indices;
  - `slice_for(universe, today, now, slots, slice_size)` returns `ScanSlice(run_index, batch, batches, symbols)`, with `batches = ceil(U / slice_size)`, `batch = run_index mod batches`, and symbols the batch's consecutive slice of the sorted universe. An empty universe gives `batches = 0` and no symbols;
  - **Hypothesis property (SC-003)**: for any universe of 1–1000 symbols, `slice_size` 1–200 and any start date in 2026–2027, running every slot that exists for `batches` consecutive slots covers every symbol exactly once.
- [x] T011 Implement `src/trading_agent/opportunistic_identifier/rotation.py` (pure; epoch `date(2026, 1, 2)`; the module docstring documents the rule and the accepted late-start case as research O3 states them), so T010 passes. Counting slots per past session is a loop over a few hundred days: no caching, no state.
- [x] T012 [P] Integration tests first, in `tests/integration/opportunistic_identifier/test_store.py`, as `ta_opportunistic_identifier` via `as_role`:
  - `open_symbols(now)` returns only this agent's non-`no_action` symbols with `expires_at > now`;
  - `write(rows)` inserts all rows with `agent = 'opportunistic_identifier'` in one transaction, and a failing row leaves none;
  - inserting `agent = 'research'` is refused by row-level security;
  - `SELECT` on `positions`, `decisions`, `orders`, `risk_verdicts`, `account_snapshots` and `journal` each raises `InsufficientPrivilege`.
- [x] T013 Implement, in `src/trading_agent/opportunistic_identifier/service.py`: `OIStore` protocol, `ReportRow`, `NotAutocommit`, and `PgOIStore(conn, *, _allow_savepoints=False)` with `open_symbols` and `write`, built like `research/service.py`'s `PgResearchStore`, so T012 passes.

**Checkpoint**: adapter, config, rotation and store are done. Commit each pair (T006–T007, T008–T009, T010–T011, T012–T013) separately.

---

## Phase 3: User Story 1 — An hourly undervaluation scan that leaves a record (P1) 🎯 MVP

**Goal**: one run on fake data writes one buy report per valid proposal, with code-built sources, or one `no_action`. A `no_action` row doesn't wake the PM.

**Independent test**: the service with `FakeOIMarketData` and `FakeModel` writes exactly the expected rows. The shortlist matches a hand-computed average of ranks. A newer `no_action` row leaves `latest_report_time` unchanged.

### Tests for User Story 1

- [x] T014 [P] [US1] `tests/unit/opportunistic_identifier/test_screen_eligibility.py`, a table test of `screen.assess(symbol, listing, profile, quote, fundamentals, now, universe, quote_max_age)`. One case per skip reason:
  - **listing first** (research O5 step 1): `screen.listing_stop(symbol, listing)` gives `not_listed`, `share_class_unverified`, `conflicting_listing`, `missing_type`, `missing_mic`, or `universe_listing` (an ETF; an OTC `mic`) **before** any per-name call. A service test (T019) asserts no call is made for those names;
  - `stale_quote`: `t` yesterday; `t` 16 minutes old; `t` missing;
  - `missing_price`: `c` or `pc` missing or zero;
  - every `reference.normalize` failure reason reachable from these inputs;
  - `universe_market_cap`, `universe_dollar_volume`, `universe_share_price`, each just under its floor, while exactly at the floor passes;
  - `missing_52_week_high`;
  - `missing_fundamentals`: neither `peTTM` nor `pbQuarterly`. Either one alone passes;
  - `implausible_move`: +51% and −51%, while ±50% passes.

  An eligible name returns a `Candidate` with `move_today = (c − pc) / pc` and `below_high = (52WeekHigh − c) / 52WeekHigh`, which is negative when `c` is above the high.
- [x] T015 [P] [US1] `tests/unit/opportunistic_identifier/test_screen_gate_equivalence.py`, a **Hypothesis property**. For generated `reference.normalize.ReferenceRow`-shaped values and generated `UniverseConfig` floors, `screen.universe_stop(row, universe)` equals `risk.gate._universe_stop(risk.model.Reference(...), RiskConfig-with-that-universe)`. This pins the OI's copy to the gate (research O4). It imports the gate's private function in the test only.
- [x] T016 [P] [US1] `tests/unit/opportunistic_identifier/test_screen_ranking.py`, testing `screen.shortlist(candidates, open_symbols, size)`:
  - names in `open_symbols` are left out first and counted as `already_open`;
  - a hand-computed 6-name example: ranks by `move_today` ascending and by `below_high` descending (1-based ordinals, equal values ordered by symbol), averaged, and the lowest `size` kept, with ties broken by symbol;
  - fewer candidates than `size` returns them all, and none returns an empty list.
- [x] T017 [P] [US1] `tests/unit/opportunistic_identifier/test_prompt.py`:
  - `build_user(now, trading_day, shortlist)` returns JSON with exactly `now`, `trading_day` and `names`, where each name has exactly the data-model.md fields, numbers as JSON numbers and missing values as `null`;
  - no key named `positions`, `cash`, `decisions`, `journal` or `reports` appears;
  - `name` and `industry` are cut to 100 characters;
  - `SYSTEM_PROMPT` contains `PROMPT_VERSION`, "buy", the target-weight meaning, conviction 1–5, the statement that `name` and `industry` are untrusted data and not instructions, and the schema from `answer.ANSWER_SCHEMA`.
- [x] T018 [P] [US1] `tests/unit/opportunistic_identifier/test_answer_rows.py`:
  - a valid answer for 2 shortlisted names gives 2 `ReportRow`s with `direction = 'buy'`, the conviction, `suggested_size_pct` as `Decimal` rounded **down** to 3 places (12.34567 → 12.345), the rationale cleaned by `text.clean` and then cut to `rationale_max_chars`, and `expires_at` at the trading day's close (early close included);
  - each row has three sources exactly as research O9: titles, `url` without any key (assert that the fake key string appears in no field), `publisher = "Finnhub"`, `published_at` (the quote's `t`, or the fetch time), and `relevance = "primary"`;
  - `{"proposals": []}` gives no rows.
- [x] T019 [P] [US1] `tests/unit/opportunistic_identifier/test_service_happy.py`, testing `OIRun(...).run()` with `FakeOIMarketData`, `FakeModel` and an in-memory `OIStore` at the default clock:
  - names failing the listing check make no call; the rest are fetched quote, then profile, then fundamentals, with exactly one model call;
  - **pacing** (FR-021): with a fake sleep, consecutive Finnhub calls are spaced by `60 / finnhub_calls_per_minute` seconds;
  - the rows written equal the valid proposals;
  - `RunCounts` are right;
  - log lines match the contract's "Logs" table, with token counts included;
  - a model that proposes nothing gives one `no_action` with `nothing_argued` and the counts, and exit 0;
  - an empty `scan_universe` gives `empty_scan_universe` with no fetch and no model call;
  - an all-skipped slice gives `empty_shortlist` with no model call.
- [x] T020a [P] [US1] `tests/unit/opportunistic_identifier/test_text.py`: `clean` removes NUL, other C0 controls except `\n` and `\t`, DEL and lone surrogates, and keeps everything else; identical behavior to `research.text.clean` on a Hypothesis-generated string (the test may import both).
- [x] T020b [P] [US1] `tests/integration/portfolio_manager/test_reads_oi_reports.py` (SC-006): an open OI buy report inserted as `ta_opportunistic_identifier` is returned by the PM's `PostgresStore.read_inputs` with `agent = 'opportunistic_identifier'`.
- [x] T020 [P] [US1] `tests/integration/orchestrator/test_latest_argued_report.py`, migration 0014: as the migration admin, insert a research buy report at T1 and then an OI `no_action` at T2 > T1. `SELECT generated_at FROM latest_report_time` as `ta_orchestrator` returns T1. A later OI buy at T3 returns T3. A future-dated report is still ignored. `ta_assistant` and `ta_dashboard` can still read the view.

### Implementation for User Story 1

- [x] T021 [US1] `src/trading_agent/opportunistic_identifier/text.py` (copy of `research/text.py`'s `clean`, with its docstring citing Research's review H1) and `screen.py` (pure): `Skip`, `Candidate`, `listing_stop` (O5 step 1), `assess` (then freshness, `reference.normalize.normalize`, `universe_stop`, the 52-week high, fundamentals and move checks; `name` and `industry` cleaned and cut to 100 characters), `universe_stop` (the five-line copy of the gate's comparisons, using `risk.rules` rule names and `US_LISTED_MICS`), and `shortlist`. Make T014–T016 and T020a pass.
- [x] T022 [US1] `src/trading_agent/opportunistic_identifier/answer.py` (pure): `ANSWER_SCHEMA` generated from the contract's answer table (`direction` enum `["buy"]`); `check(text, shortlist, open_symbols, rationale_max_chars) -> Checked` (valid path now; drops in US2); `rows(checked, data_by_symbol, trading_day) -> list[ReportRow]` building sources as in research O9. Make T018 pass.
- [x] T023 [US1] `src/trading_agent/opportunistic_identifier/prompt.py`: `PROMPT_VERSION = "0.1"`, `SYSTEM_PROMPT` (research O7, schema appended) and `build_user`. Make T017 pass.
- [x] T024 [US1] `OIRun` in `src/trading_agent/opportunistic_identifier/service.py`:
  1. reads open symbols;
  2. computes the slice;
  3. fetches the symbol list once, applies `listing_stop`, then fetches each remaining name, paced at `finnhub_calls_per_minute` (copy the pacer pattern of `research/service.py`), skipping profile and fundamentals after a stale quote, and starting no call after `config.fetch_deadline(start)`;
  4. screens and builds the shortlist;
  5. checks `max_input_chars`;
  6. makes one model call, logging tokens;
  7. checks the answer and writes the rows or one `no_action`, with counts in the rationale.

  Make T019 pass. Keep `service.py` under 450 lines, splitting the fetch loop into `fetch.py` if needed.
- [x] T025 [US1] `src/trading_agent/opportunistic_identifier/__main__.py`:
  - reads only the contract's variables, named and never echoed;
  - loads the config;
  - `build_model(...)` as in `research/__main__.py`, with `require_https_base_url` for the Qwen URL;
  - connects with `storage.db.connect` (autocommit);
  - runs once;
  - maps outcomes to the contract's exit codes.

  Tests in `tests/unit/opportunistic_identifier/test_main.py`: missing or invalid variables → exit 2, naming the variable without its value; unknown argument → exit 2; a happy run → exit 0; with `ANTHROPIC_API_KEY`, `RESEARCH_DASHSCOPE_API_KEY` and `FINNHUB_API_KEY` set but the OI's own unset → exit 2 (FR-020: non-prefixed and other agents' variables are never used).
- [x] T026 [US1] `src/trading_agent/storage/migrations/0014_latest_argued_report.sql`: `CREATE OR REPLACE VIEW latest_report_time AS SELECT max(generated_at) AS generated_at FROM reports WHERE generated_at <= now() AND direction <> 'no_action';`, with a header comment citing spec FR-023, research O13 and ADR 0011, and saying the grants are unchanged. Make T020 pass, and run the full `tests/integration/orchestrator` suite.
- [x] T027 [US1] Docs for FR-023:
  - one line in `docs/specs/orchestrator.md` Inputs: "a `no_action` report doesn't count as new (`specs/011-opportunistic-identifier` FR-023)";
  - a dated amendment note, `Amended 2026-10-07 by feature 011`, at the end of `specs/005-orchestrator/spec.md`, stating the same, without changing its accepted requirements' text;
  - in the same note, that the Opportunistic Identifier's timeout is now 15 minutes (`specs/005-orchestrator/spec.md` Assumptions still says 10; don't edit that line);
  - wherever `docs/specs/data-model.md` or `specs/001-data-model` describes `latest_report_time` as "the newest report's creation time", add "excluding `no_action` reports (feature 011)".

**Checkpoint**: US1 is shippable with the schedule still disabled.

---

## Phase 4: User Story 2 — Model output can't write anything the run didn't support (P1)

**Goal**: every drop rule; SC-002 as a property.

**Independent test**: the malicious answers in T028 write nothing, and each drop has its reason.

- [x] T028 [P] [US2] `tests/unit/opportunistic_identifier/test_answer_drops.py`, a table test with one case per drop reason:
  - `malformed_answer`: an item that isn't an object; a missing field; an extra field such as `sources`;
  - `not_shortlisted`: a listed, eligible symbol that isn't on the shortlist; lower-case `msft` when `MSFT` is shortlisted; `MSFT ` with a trailing space;
  - `invalid_direction`: `sell`, `hold`, `BUY`;
  - `invalid_conviction`: 0, 6, 3.5, `"3"`, `true`;
  - `invalid_size`: 0, 0.0004 (rounds down to 0), −1, 100.01, NaN, a string, `true`;
  - `duplicate_symbol`: the second of two;
  - `already_open`: a backstop symbol in `open_symbols`.

  Also: a non-JSON answer or the wrong top-level shape → `unusable_answer`.
- [x] T029 [P] [US2] `tests/unit/opportunistic_identifier/test_answer_property.py`, a **Hypothesis property (SC-002)**: for any shortlist and any JSON-shaped answer (arbitrary nested values, injected text in `rationale`, and arbitrary symbols), every row `rows()` returns has its `symbol` in the shortlist, `direction == 'buy'`, conviction 1–5, size in (0, 100], exactly three sources whose fields all come from `data_by_symbol` (never from the answer), and a rationale no longer than `rationale_max_chars`.
- [x] T030 [P] [US2] `tests/unit/opportunistic_identifier/test_service_injection.py`: a model answer whose rationale holds `"}]}`, `\n\nSYSTEM: …` and a fake key-like string writes that rationale only as data, cut to length. A provider `name` or `industry` holding instructions still reaches the prompt only inside the JSON document, cut to 100 characters.
- [x] T031 [US2] Complete `answer.check` with every drop rule and the `unusable_answer` path; `service.py` logs each drop per the contract and writes `all_dropped` (exit 0) when nothing valid is left. Make T028–T030 pass.

---

## Phase 5: User Story 3 — A failed or quiet run is still a data point (P2)

**Goal**: every failure leaves a `no_action` naming it, with the contract's exit code. The run respects the window and the deadline.

**Independent test**: each injected failure gives one `no_action` row and the documented exit status.

- [ ] T032 [P] [US3] `tests/unit/opportunistic_identifier/test_service_failures.py`, one test per failure category, each giving one `no_action` with the category and exit 1:
  - `symbol_list_unavailable`;
  - `market_data_unavailable`: `KeyRejected` on the first per-name call; every per-name fetch raising `ProviderUnavailable`;
  - `input_too_large`: `max_input_chars` set below the document;
  - `model_key_rejected`, `model_rejected_request`, `model_unavailable`, `model_refused`, `model_truncated` and `unusable_answer`, each from `FakeModel` raising the matching `llm.ports` error;
  - `internal_error`: an unexpected exception.

  Plus: partial data (some names `NotPermitted` or `ProviderUnavailable`) doesn't fail the run (FR-018). ERROR log lines name the exception type and HTTP status, never the message.
- [ ] T033 [P] [US3] `tests/unit/opportunistic_identifier/test_service_window_deadline.py`:
  - **window**: a weekend, a holiday, 09:45 ET, or after the close → nothing fetched, logged, exit 0. 10:00 ET and 15:59 ET run. On the early-close day, 13:01 ET does nothing;
  - **window closed**: the close passing mid-run → nothing written, `window_closed` logged at ERROR, **exit 5**;
  - **deadline**: with a fake clock that advances per call, no call starts after `config.fetch_deadline(start)` (650 s on Qwen, 530 s on Anthropic with the shipped config), unfetched names count as `not_fetched`, and the model call still happens with what was fetched;
  - **rate limit**: `RateLimited` backs off and continues until the deadline.
- [ ] T034 [P] [US3] `tests/unit/opportunistic_identifier/test_main_exit.py`: a database unreachable or a failed read or write → exit 3. The `no_action` write itself failing → exit 3. An exception escaping before any write → exit 4, logged CRITICAL with its type only.
- [ ] T035 [US3] Implement the failure mapping, the window check (research O12) and the deadline in `service.py` and `__main__.py`, so T032–T034 pass.

---

## Phase 6: User Story 4 — The owner measures real token use before switching it on (P2)

**Goal**: `--dry-run` and `--check` as in the contract; token counts on every run.

**Independent test**: a dry run with fakes writes nothing and prints the fake model's token counts.

- [ ] T036 [P] [US4] `tests/unit/opportunistic_identifier/test_main_dry_run.py`:
  - `--dry-run` prints JSON lines `slice`, `skip`, `shortlist` (symbol, both ranks, score), `would_write`, `dropped` and `summary`, including `input_tokens` and `output_tokens`;
  - the store's `write` is never called;
  - it runs outside the window;
  - without `OPPORTUNISTIC_IDENTIFIER_DATABASE_URL`, `open_symbols` is empty and it still runs;
  - no line contains the fake key values.
- [ ] T037 [P] [US4] `tests/unit/opportunistic_identifier/test_main_check.py`:
  - `--check AAPL MSFT` prints, per symbol, the raw metric keys received, the derived values, and `eligible` or the skip reason;
  - no model client is built, and neither the database variable nor the model variables are required;
  - more than 10 symbols, or an implausible ticker → exit 2.
- [ ] T038 [US4] Implement `--dry-run` and `--check` in `__main__.py`, adding `dry_run.py` if `__main__.py` would pass 300 lines, so T036–T037 pass.

---

## Phase 7: Rollout wiring (shipped disabled) and docs

- [ ] T039 [P] Login: add `Login("ta_opportunistic_identifier_login", "ta_opportunistic_identifier")` to `LOGINS` in `src/trading_agent/storage/logins.py` (update the comment to "nine rows"). Update `tests/unit/storage/test_logins.py` and `tests/integration/storage/test_logins.py` to nine. Add a dated amendment note to `specs/010-observe-only-deployment/contracts/logins-command.md`.
- [ ] T040 [P] Schedule: in `config/schedule.yaml`, fill `opportunistic_identifier.env` with the five `OPPORTUNISTIC_IDENTIFIER_*` names, each with the same style of comment as Research's (the timeout was set in T007a). **Keep `enabled: false`**, and update the header comment ("the OI exists (feature 011) and ships disabled; a separate PR enables it after the owner's dry run"). Run `pytest tests/unit/orchestrator`.
- [ ] T041 [P] Deployment: add the five names as `preserve()` to the orchestrator service's `env` in `.railway/railway.ts`. Add them to the pinned orchestrator tuple in `tests/unit/deploy/test_deployed_shape.py`, and update `.railway/README.md`'s variable list if it names Research's. Run `pytest tests/unit/deploy`.
- [ ] T042 [P] Behavior spec: update `docs/specs/opportunistic-identifier-agent.md` to point at this feature, as `docs/specs/research-agent.md` does. Cover: the owner's scan list; design A (code screens, one model call on about 20 names); buy only; the two-rank ranking; the 15-minute quote limit; open names left out; Qwen by default; its own Finnhub key on a shared account; and quiet runs not waking the PM. Keep the existing non-goals.
- [ ] T043 [P] Update the Opportunistic Identifier row in `docs/architecture/overview.md` to reflect this feature's inputs and cadence (no new design).

---

## Phase 8: Polish & cross-cutting concerns

- [ ] T044 `tests/unit/opportunistic_identifier/test_import_guard.py`, like Research's: the package imports nothing from `research`, `portfolio_manager`, `orchestrator` or `execution`, and pure modules import no `psycopg`, `urllib`, `anthropic` or `os`.
- [ ] T045 Run `scripts/lint.sh`, the full unit suite and the integration suite. All green, no module over 600 lines, no `# noqa`.
- [ ] T046 Walk quickstart.md step 1 and confirm each command passes. Steps 2–4 are the owner's.

---

## Dependencies & execution order

- **Phase 1** first. T001–T004 are independent.
- **Phase 2** blocks every story. Order within it: T005, then T006→T007 → T007a → T008→T009 (the config test reads `finnhub.TIMEOUT_SECONDS` and the 15-minute schedule). T010→T011 and T012→T013 can run in parallel with that chain.
- **US1 (Phase 3)** needs Phase 2. Within it: T014–T020 tests, then T021 → T022 → T023 → T024 → T025. T026–T027 are independent of T021–T025.
- **US2** needs T022 and T024. **US3** needs T024–T025. **US4** needs T025.
- **Phase 7** is independent of US2–US4, but merges after them. **Phase 8** comes last.

## Parallel examples

- **Phase 2**: T006, T010 and T012 (three test files) at once; T008 after T007a.
- **US1 tests**: T014, T015, T016, T017, T018 and T020 at once, then T019 once the fake exists.
- **Phase 7**: T039–T043 at once.

## Implementation strategy

1. **MVP = US1** with the schedule disabled: a dry-runnable agent and the FR-023 view change.
2. **US2** (safety of what's written) before any enable, since it's the SC-002 guarantee.
3. **US3, then US4.** US4's `--check` and `--dry-run` are what the owner needs before enabling.
4. **Phase 7** lands the wiring with `enabled: false`. The enable PR is separate (quickstart step 4).

**Subagent handoff** (CLAUDE.local.md): one phase per Sonnet subagent turn, reviewed between phases. Review line by line T015 and T021's `universe_stop` (the copy of the gate's rule), and T026 (the view the orchestrator triggers on).
