---

description: "Task list for the journal writer (feature 012)"
---

# Tasks: Journal writer

**Input**: Design documents from `/specs/012-journal-writer/`

**Prerequisites**:
- plan.md, spec.md (with Clarifications), research.md (J1–J14), data-model.md, quickstart.md;
- contracts/journal-interface.md, contracts/attribution.md, contracts/summary-template.md;
- ADRs [0002](../../docs/adr/0002-pm-synthesizes-rather-than-analysts-deciding.md), [0004](../../docs/adr/0004-shared-postgres-role-scoped-credentials.md), [0016](../../docs/adr/0016-market-data-for-the-llm-agents.md), [0021](../../docs/adr/0021-railway-deployment-as-code-observe-only-first.md), [0022](../../docs/adr/0022-journal-writer-runs-after-the-close-with-its-own-finnhub-key.md).

**Tests**: included, written first within each task (Constitution: every logic change is covered by tests). SC-002 (the arithmetic) and SC-003 (no agent text in the summary) are backed by fixtures and Hypothesis properties of pure modules.

**Organization**: one phase per user story in spec.md.
- **US1**: the books, end to end through the service with fakes.
- **US2**: usage counts and the summary.
- **US3**: failures, exit codes, the CLI, re-runs and missed sessions.
- **US4**: `--dry-run` and `--check`.
- **Phase 7**: the login, deployment wiring and docs.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4

## Conventions every task follows

- **No task reaches the network or calls a model.** Quotes come from the existing `tests/fakes/market_data.py` (`FakeMarketData`, which implements `get_quote`), extended only if a case needs it. The suite-wide network guard in `tests/conftest.py` stays as it is.
- **Never read, print or grep for real credentials or `.env` files.** Tests set fake values with `monkeypatch.setenv`, such as `JOURNAL_FINNHUB_API_KEY=fake-not-real`.
- **Pure modules** (`books.py`, `usage.py`, `facts.py`, `summary.py`, `state.py`) never import `psycopg`, `urllib`, `os`, `prices`, `store` or `service`, and never read the clock: dates and times are arguments. `books.py` takes a `previous_session(day) -> date` callable rather than importing the calendar, so tests can pass the real one or a stub.
- **Imports**: only `trading_agent.risk.calendar`, `trading_agent.reference.finnhub`, `trading_agent.reference.provider` and `trading_agent.storage.db` from other packages (research J13). Never another top-layer package, `llm`, `risk.gate`, `risk.rules` or `execution`.
- **Arithmetic** is `Decimal` throughout, never `float`. Rounding happens only in `state.py` when encoding (research J5 step 7: weights 6 places, index 8, `day_return` and `account.return` 6).
- **Test clock**: **Friday 2026-10-09, 18:30 ET = 22:30 UTC** (EDT), unless the test is about another day.

  | Case | Date and time | Detail |
  |---|---|---|
  | Default | Fri 2026-10-09, 22:30 UTC | open 13:30 UTC, close 20:00 UTC |
  | Before the close | Fri 2026-10-09, 19:59 UTC | `before_close` |
  | Weekend | Sat 2026-10-10 | `not_a_session` |
  | Holiday | Thu 2026-11-26 | `not_a_session` |
  | Early close | Fri 2026-11-27, 22:30 UTC | EST, close 13:00 ET = 18:00 UTC |
  | Missed session | previous row Wed 2026-10-07, run Fri 2026-10-09 | `sessions_covered` 2, missed `2026-10-08` |
  | Holding limit | support Fri 2026-10-02, run Fri 2026-10-09 | 5 sessions since: exits |
- **Test connections**: integration tests use feature 001's rolled-back `conn` fixture (`tests/integration/conftest.py`), `as_role` (`tests/integration/helpers.py`), and the row builders in `tests/integration/storage/factories.py` and `chain.py`. They skip without `TEST_DATABASE_URL`. Use a throwaway `postgres:16` container (for example on 127.0.0.1:55432), never the owner's container on port 5433.
- **Mutation-check only the safety-relevant tests**: T013 (no agent, broker or attribution text in the summary, which the PM reads) and T020 (the login's limits). Break the code on purpose, confirm a test fails, then restore the file's saved text with a direct edit, never `git checkout`.
- **Testing in two tiers** (CLAUDE.md): after each task, run that task's tests and `scripts/lint.sh`. The full unit and integration suites run once, in T023.
- **Module size**: no module over 600 lines (`scripts/check_module_size.py`). If `service.py` nears 400, move the J4 window and read assembly into `store.py`.
- **Quality gate**: no `# noqa`, and no raised limits.
- **Commits**: one per task or tight task group, in the style `Journal (feature 012): what changed (Tnnn)`, staged by explicit path. Never stage `src/trading_agent/risk/__main__.py` or `job_*.csv`, which belong to the owner.
- **Not edited**: `src/trading_agent/risk/*`, `src/trading_agent/reference/*`, any migration, any grant.

---

## Phase 1: Setup

- [x] T001 Create the package and its layer:
  - `src/trading_agent/journal/__init__.py` with a one-paragraph docstring pointing at spec 012 and ADR 0022;
  - add `journal` to the first layer of `[tool.importlinter]` in `pyproject.toml`, beside `opportunistic_identifier`;
  - `tests/unit/journal/__init__.py`, `tests/integration/journal/__init__.py`;
  - `tests/unit/journal/test_imports.py`: walks `src/trading_agent/journal/*.py` with `ast`, and fails on any import outside the conventions' list. Also fails on `psycopg`, `urllib`, `os`, `datetime.now`, `prices`, `store` or `service` in the five pure modules.

  Run `scripts/lint.sh`.
- [x] T002 [P] Config, contracts/journal-interface.md "Configuration":
  - `config/journal.yaml` with `holding_sessions: 5`, `finnhub_calls_per_minute: 20`, `fetch_deadline_seconds: 480`, `close_grace_minutes: 5`, each with a one-line comment;
  - `src/trading_agent/journal/config.py`: `load_config(path) -> JournalConfig` (a frozen dataclass), `DEFAULT_CONFIG_PATH`, and `JournalConfigError`. Every key is required, unknown keys are refused, and the values are integers (not booleans) within bounds: 1–60, 1–300, 30–540, 0–30;
  - `tests/unit/journal/test_config.py`: the shipped file loads; each missing, unknown, wrong-typed and out-of-bounds key is refused by name.

---

## Phase 2: Foundational (blocks every story)

- [x] T003 [P] `src/trading_agent/journal/model.py`, frozen dataclasses only, no logic:
  - `Holding(symbol, weight_pct: Decimal, ref_price: Decimal, support_session: date)`;
  - `Book(agent, started_on: date, index: Decimal, holdings: dict[str, Holding])`;
  - `ReportRow(id, agent, generated_at, symbol, direction, suggested_size_pct, support_session: date, late: bool)`;
  - `Price(symbol, close: Decimal | None, reason: str | None)`, where the reason is one of `not_today`, `no_price`, `not_permitted`, `rate_limited`, `unavailable`, `deadline`, `malformed`;
  - `BookResult` (per agent: the new book, `day_return`, `scaled_by`, `late_reports`, `unpriced`, `skipped_targets`, `exited_sell`, `exited_holding_limit`);
  - `DayFacts` (the summary's inputs, research J10) and `RunOutcome` (contracts/journal-interface.md).
- [x] T004 [P] `src/trading_agent/journal/state.py` (research J11, contracts/attribution.md), test first in `tests/unit/journal/test_state.py`:
  - `decode_books(attribution: dict) -> dict[str, Book]`, refusing any `schema_version` other than `1` with `UnknownSchema`;
  - `encode(...) -> dict`, producing exactly the documented object, with numbers as strings rounded per the conventions, symbols sorted, and `missed_sessions` capped at 30;
  - the tests: a round trip; the documented example decodes; the unknown version is refused; and a Hypothesis property that `decode(encode(x))` re-encodes identically, which is what makes a re-run identical (SC-004).
- [x] T005 `src/trading_agent/journal/prices.py` (research J2, J8), test first in `tests/unit/journal/test_prices.py` using `FakeMarketData` and injected `sleep` and `monotonic`:
  - `fetch_closes(provider, symbols, day, cfg, *, sleep, monotonic) -> dict[str, Price]`, in symbol order, paced at `60 / finnhub_calls_per_minute` seconds;
  - **accepted** only when `current` is usable and `open_time(day) ≤ t ≤ close_time(day) + close_grace_minutes`; otherwise `not_today` (or `no_price` if `current` is None);
  - `RateLimited` and `ProviderUnavailable` retried at most twice, one pacing interval apart; `NotPermitted` is unpriced at once;
  - a symbol not matching `^[A-Z][A-Z0-9.\-]{0,9}$` is never requested and is `malformed`;
  - `KeyRejected` propagates;
  - symbols not reached by `fetch_deadline_seconds` are `deadline`;
  - the tests also cover: a quote stamped after the close plus grace is refused; one stamped mid-afternoon is accepted; the early-close day uses 18:00 UTC; no symbol is fetched twice beyond the retries.
- [x] T006 `src/trading_agent/journal/store.py` (data-model.md "Read", research J11), with `tests/integration/journal/test_store.py`:
  - `PgJournalStore(conn).read(day, open_at, close_at, previous_close_of)` returns, in **one** `REPEATABLE READ, READ ONLY` transaction (never two):
    - the previous row (latest with `trading_day < day`) and whether any row has `trading_day > day`;
    - the J4 window, computed inside that transaction from the previous row: `(previous_close_of(previous_day), close_at]`, or, with no previous row, reports whose New York date is `day` and `generated_at ≤ close_at`. `previous_close_of` is `calendar.close_time`, passed in;
    - the window's reports (only the columns in data-model.md, never `rationale_md` or `sources`);
    - their `decision_reports`, decisions, verdicts and orders;
    - that day's decisions, verdicts, orders, refusals, triggers and account snapshots;
  - `upsert(row)` is one `INSERT … ON CONFLICT (trading_day) DO UPDATE` setting every column and `written_at = now()`;
  - the integration tests, as `ta_journal`: the reads return the right rows across a day boundary in New York time; an upsert twice leaves one row with the second values; the SQL text never names `rationale_md`, `reasoning_md`, `sources`, `broker_reason`, `details` or `system_state` (asserted on the module source).

---

## Phase 3: User Story 1 - Each agent's book, valued every close (Priority: P1) 🎯 MVP

**Goal**: valued books per agent, written in today's row.

**Independent Test**: a multi-session run with fakes, checked against hand-computed books (spec US1 scenarios 1–7).

- [x] T007 [P] [US1] Tests first, in `tests/unit/journal/test_books.py`:
  - every US1 acceptance scenario, including the 10.89% drift;
  - a three-session fixture with hand-computed indexes, written out in the test as comments;
  - scale-down at 150%;
  - a sell above the current weight changes nothing, and a sell on an unheld symbol does nothing;
  - hold sets the weight; reports apply in order: a buy at 5% then a sell to 2% on an unheld symbol leaves 2% with the buy's support session, and a sell then a buy leaves the buy's weight;
  - an unpriced holding returns 0 and drifts; an unpriced new target is skipped; an unpriced exit leaves at its last price;
  - the holding limit at exactly 5 sessions exits and at 4 stays, a buy restarts the count, and a sell doesn't;
  - holdings use the `support_session` on each `ReportRow` (set by the service, T009), and the late count uses its `late` flag;
  - a new agent starts at 100 with a day return of 0.

  Hypothesis properties: weights stay ≥ 0 and sum to ≤ 100 after every step, also after encoding with `ROUND_DOWN` and decoding again across several sessions; `1 + R > 0`; the index never goes negative.
- [x] T008 [US1] `src/trading_agent/journal/books.py` (research J5, J6), making T007 pass:
  - `sessions_since(a, b, previous_session, limit) -> int`;
  - `advance(book, reports, prices, day, holding_sessions, previous_session) -> BookResult`, applying J5's steps in order, reading `support_session` and `late` from each `ReportRow`;
  - `new_book(agent, day) -> Book`.
- [x] T009 [US1] `src/trading_agent/journal/service.py`, the run's skeleton, test first in `tests/unit/journal/test_service.py` with an in-memory fake store (`tests/fakes/journal_store.py`, new) and `FakeMarketData`:
  - the J3 gate: `nothing_to_do` for `not_a_session` and `before_close`;
  - the store's read (T006), which computes the J4 window;
  - each `ReportRow`'s `support_session` and `late` (research J4): step back from `D` with `calendar.previous_session` to the first session whose `close_time` is at or after `generated_at`;
  - books for every agent in the previous row plus every agent with a window report;
  - the symbols to price (every held symbol plus every buy or hold target), fetched once;
  - `advance` for each book;
  - equity open and close and the account return (J7);
  - the encoded attribution;
  - one upsert.

  For now the summary is a placeholder string; T014 replaces it. Tests: first-ever run; a normal second day; agents present in only one of the two sources; the row's equity columns per J7 (latest at or before the open, else earliest; latest of the day); the account return on the first-ever run uses `equity_open`, and is null when its base is 0; support sessions for a report after Thursday's close (Friday, not late) and a weekend report with a missed Monday (Monday, late on Tuesday).

**Checkpoint**: books are written end to end with fakes. Run `tests/unit/journal` and lint.

---

## Phase 4: User Story 2 - The day's summary and the PM's usage (Priority: P1)

**Goal**: usage counts in the attribution data; a fixed, bounded summary with no agent text.

**Independent Test**: fixture rows for one day produce the exact expected summary, and planted instructions never appear.

- [x] T010 [P] [US2] `src/trading_agent/journal/usage.py` (research J9), test first in `tests/unit/journal/test_usage.py`:
  - `usage(reports, decision_reports, verdicts, orders) -> dict[agent, counts]`;
  - spec US2 scenario 2; a decision citing two reports from one agent counts once in `cited_decisions`; a decision citing both agents counts once for each; `filled` needs `fill_qty > 0` (a `partially_filled` order counts, an `expired` one with 0 doesn't).
- [x] T011 [P] [US2] Tests first, in `tests/unit/journal/test_summary.py`:
  - the contract's example day renders character for character;
  - US2 scenarios 1, 3 and 4;
  - the breaker reads `triggered` for a `daily_loss_halt` verdict and for a `daily_loss_line_crossed` refusal, else `not triggered`;
  - a rejection rule not matching `^[a-z_]{1,40}$` counts as `other`;
  - a malformed ticker is counted, never shown;
  - empty sections read `none`;
  - stop-loss verdicts count only on the stop-loss line; orders from both decisions and stop-loss verdicts count on the Orders line; a decision with no verdict counts as neither approved nor rejected; `equity_open` 0 shows `n/a`;
  - a 500-decision, 40-rule, 30-missed-session day stays at most 2,000 characters, with the fixed lines whole.
- [x] T012 [US2] Two pure modules, making T011 pass:
  - `src/trading_agent/journal/facts.py`: `day_facts(rows, day, missed_sessions, unpriced_count) -> DayFacts`, applying research J10's day and counting rules to the read rows. It takes the store's row dicts and copies only numbers, dates, closed-set codes and well-formed tickers into `DayFacts`;
  - `src/trading_agent/journal/summary.py`: `SUMMARY_VERSION = "0.1"` and `render(facts: DayFacts) -> str`.

  `DayFacts` carries only numbers, dates, closed-set codes and tickers, so the renderer can't reach agent text.
- [x] T013 [US2] The SC-003 guard, in `tests/unit/journal/test_summary_no_agent_text.py`:
  - a Hypothesis property: fixture rows whose `rationale_md`, `reasoning_md`, source titles, `broker_reason` and refusal `details` hold random text plus a fixed marker (`IGNORE-PREVIOUS-INSTRUCTIONS`) are run through `facts.day_facts` and `render`, and the marker never appears;
  - also: no agent name, index or return appears in the summary (spec, clarify Q1).

  **Mutation-check**: make `day_facts` copy one rationale into `DayFacts` and the renderer print it; the test must fail. Restore by direct edit.
- [x] T014 [US2] Wire it in `service.py`: call `facts.day_facts` on the read rows, render the summary, and add `usage` per agent and `summary_version` to the attribution. Extend `test_service.py`: the written row's summary and usage match the fixture day.

**Checkpoint**: a complete row with fakes. Run `tests/unit/journal` and lint.

---

## Phase 5: User Story 3 - A run that can't finish leaves the record honest (Priority: P2)

**Goal**: every failure writes nothing and names itself; re-runs and missed sessions are handled.

**Independent Test**: inject each failure and check no write and the exit code; run a day twice and compare.

- [x] T015 [US3] Failures in `service.py`, test first in `test_service.py`. Each must leave no upsert and a named `RunOutcome`:
  - `no_account_snapshot`;
  - `no_prices` (at least one symbol needed, none priced);
  - `market_data_key_rejected`;
  - `future_row`;
  - `unknown_schema`.

  Also: some unpriced symbols are not a failure; missed sessions are listed and logged at WARNING, with `sessions_covered` and the books valued across the gap (US3 scenario 3); a re-run of the same day computes from the previous day's row, not today's (US3 scenario 2).
- [x] T016 [US3] `src/trading_agent/journal/__main__.py` (contracts/journal-interface.md, research J12), test first in `tests/unit/journal/test_main.py`, built like `opportunistic_identifier/__main__.py`:
  - `main(argv, *, market_factory, connect, config_path, clock, sleep, monotonic, out) -> int`;
  - `JOURNAL_*` variables only, named when missing and never echoed;
  - exit codes 0–4, with any exception becoming 4 and a `psycopg.Error` becoming 3;
  - log lines exactly as the contract lists, exceptions by type only.

  Tests: each exit code; a missing variable is named; no variable value appears in any log record (caplog).
- [x] T017 [US3] `tests/integration/journal/test_run.py`, against Postgres with fake quotes:
  - a full run writes one row that the PM's role can read;
  - running the same day twice leaves one identical row apart from `written_at` (SC-004);
  - a run with no snapshot writes nothing and leaves the previous rows untouched.

**Checkpoint**: run `tests/unit/journal`, `tests/integration/journal` and lint.

---

## Phase 6: User Story 4 - The owner checks a run before relying on it (Priority: P3)

**Goal**: `--dry-run` and `--check`.

**Independent Test**: dry run with fakes writes nothing and prints the row.

- [x] T018 [US4] `--dry-run` in `__main__.py` and `service.py`: the same run with the upsert replaced by printing the would-be row as JSON lines (equity, the summary, the attribution). It needs `JOURNAL_DATABASE_URL`. Test: no upsert call, and the output parses as JSON.
- [x] T019 [US4] `src/trading_agent/journal/check.py` and `--check SYMBOL …` (at most 20, each matching `^[A-Z][A-Z0-9.\-]{0,9}$`, else exit 2). It needs only `JOURNAL_FINNHUB_API_KEY` and prints per symbol `c`, `t`, today's open and close, and `accepted: true|false` with J2's reason. Test first in `tests/unit/journal/test_check.py`.

---

## Phase 7: Login, deployment and docs

- [x] T020 [P] The login (research J14):
  - add `Login("ta_journal_login", "ta_journal")` to `src/trading_agent/storage/logins.py`, and update its "nine rows" comment to ten;
  - update `tests/unit/storage/test_logins.py` and `tests/integration/storage/test_logins.py` to ten rows;
  - new `tests/integration/journal/test_role_limits.py`, as `ta_journal`: it can upsert `journal`; it can't insert or update `reports`, `decisions`, `risk_verdicts`, `orders`, `positions` or `account_snapshots`, and can't read `system_state`; the Risk Gate and Execution roles still can't select `journal`.

  **Mutation-check**: temporarily grant `ta_journal` select on `system_state` inside the test transaction; the test must fail.
- [x] T021 [P] Deployment wiring (research J1, J14):
  - `.railway/railway.ts`: a `journal` service with the same source and build as the others, `start: "python -m trading_agent.journal"`, `deploy: { cronSchedule: "30 22 * * 1-5", restartPolicyType: "NEVER" }`, `replicas: 1`, and `env` holding exactly `JOURNAL_DATABASE_URL: preserve()` and `JOURNAL_FINNHUB_API_KEY: preserve()`; add it to `resources`;
  - `tests/unit/deploy/test_deployed_shape.py`: the contract gains `journal`; new checks that `journal` alone has a `cronSchedule`, that it is exactly `30 22 * * 1-5`, and that its restart policy is `NEVER`; a broken copy with a cron schedule on another service, or a `JOURNAL_*` variable on another service, must fail;
  - `specs/010-observe-only-deployment/contracts/service-layout.md`: a `journal` row and a note referencing ADR 0022;
  - `.env.example`: the two variables, commented;
  - `.railway/README.md`: five services.
- [x] T022 [P] Docs, each referencing ADR 0022:
  - new `docs/specs/journal.md`, the behavior spec, short, in the style of `docs/specs/reference-data.md`;
  - `docs/specs/data-model.md` §`journal`: books, the 5-session holding limit, and that the summary carries no attribution;
  - `docs/architecture/overview.md`: a journal row;
  - `docs/policy/versioning.md`: rows for `SUMMARY_VERSION` 0.1 and the attribution `schema_version` 1;
  - `docs/operations/deployment.md`: the journal's login, key and service steps (quickstart steps 2–5);
  - `specs/010-observe-only-deployment/contracts/logins-command.md`: the tenth row.
- [ ] T023 Final validation:
  - the full unit suite and the full integration suite, once;
  - `scripts/lint.sh`;
  - `python -m trading_agent.journal --dry-run` against a local test database seeded by `tests/integration/storage/factories.py`, with a fake key, reached through an injected fake provider in a small script in the scratchpad, never against production;
  - tick every task, and record the suite results in this file's footer.

---

## Dependencies & Execution Order

- **Setup** (T001, T002) → **Foundational** (T003–T006). T003 and T004 can run in parallel; T005 and T006 need T003.
- **US1** (T007–T009) needs Foundational. T007 can be written alongside T010 and T011.
- **US2**: T010 and T011–T013 are pure and independent of US1. T014 needs T009.
- **US3** (T015–T017) needs T014.
- **US4** (T018, T019) needs T016.
- **Phase 7**: T020 and T021 can start after T001. T022 after T014. T023 is last.

### Parallel opportunities

- After T003: T004, T005, T006 and T020 in parallel.
- After Foundational: **US1's T007–T008 and US2's T010–T013 in parallel**, as two subagents. They touch disjoint files (`books.py` versus `usage.py`, `facts.py` and `summary.py`), and both meet in `service.py` only at T014.
- T021 and T022 in parallel with US3.

## Implementation Strategy

- **MVP**: Phases 1–3. Books are written with a placeholder summary; that's enough to prove the arithmetic on real rows.
- **Then**: US2 (the summary the PM reads), US3 (failures and the CLI), US4 (the owner's checks), and Phase 7.
- **Review between phases** (CLAUDE.local.md): the main session reviews each phase's commits, test results and lint before sending the next.

---

## Phase 8: Convergence

- [x] T024 Record zero usage counts for every agent that has a book, not only those with a report in the window: in `src/trading_agent/journal/service.py` (`_row`, which passes `usage(...)` to `encode`) or `usage.py`, give each agent in `results` the seven fields (`written`, `argued`, `no_action`, `cited`, `cited_decisions`, `approved`, `filled`) at 0 when it has no window report, so `agents.<name>.usage` is never `{}` (today `state._agent` writes `usage.get(agent, {})`). Add a test in `tests/unit/journal/test_service.py`: a second day where an agent with a held book has no report in the window gets all-zero usage. Per FR-015 and contracts/attribution.md `usage` (partial)

## Phase 9: Adversarial review fixes

Owner's decisions after the adversarial review are in spec.md "Session 2026-10-09 (after the adversarial review)" and research J1, J2, J11, J12. Same conventions as above.

- [x] T025 `no_prices` only for systemic failures (spec FR-019, research J2): in `src/trading_agent/journal/prices.py` split `not_today` into `stale` (`t` missing or before the open) and `after_close` (`t` after close plus grace), and use the same reasons in `check.py`. In `service.py`, fail as `no_prices` only when at least one symbol was needed, none was priced, and at least one reason is in `{rate_limited, unavailable, deadline, after_close}`; otherwise carry on with every symbol unpriced. Tests first in `test_prices.py` and `test_service.py`: a book whose only holding is `stale` (or `no_price`, `not_permitted`, `malformed`) writes a row and the holding exits at the limit; all-`after_close` fails; all-`deadline` fails.
- [x] T026 `already_written` and `--replace` (spec FR-003, research J11, J12, contracts/journal-interface.md): `store.read` also reports whether a row for `day` exists; a plain run that finds one returns `nothing_to_do` `already_written` before any quote is fetched; `store.upsert` becomes `insert(row)` (`ON CONFLICT (trading_day) DO NOTHING`, returning whether it inserted) and `replace(row)` (the current `DO UPDATE`); a plain run whose insert inserted nothing reports `already_written`. `--replace` in `__main__.py` (and not combinable with `--dry-run` or `--check`: exit 2). `--dry-run` ignores an existing row. Tests first: unit (service, main) and integration (`tests/integration/journal/test_store.py`, `test_run.py`: a second plain run leaves the first row untouched, including `written_at`; `--replace` rewrites it).
- [x] T027 The retry slot (spec FR-001, research J1): `.railway/railway.ts` `cronSchedule: "30 0,22 * * *"`; the guard test in `tests/unit/deploy/test_deployed_shape.py` pins exactly that string; update the header comment, `specs/010-observe-only-deployment/contracts/service-layout.md`, `docs/specs/journal.md`, `docs/operations/deployment.md` §8 and `specs/012-journal-writer/quickstart.md` step 5. Add a unit test in `test_service.py` that a run at 00:30 UTC on the Saturday after a Friday session (2026-10-10 00:30 UTC = Friday 20:30 ET) writes Friday's row, and that 00:30 UTC on Monday 2026-10-12 (Sunday evening in New York) is `not_a_session`.
- [ ] T028 Validate a stored row on decode (research J11): in `src/trading_agent/journal/state.py` `decode_books` raises `UnknownSchema` unless every number is finite; every weight is in [0, 100] and each book's weights sum to at most 100; `ref_price > 0`; `index > 0`; `started_on` and every `support_session` are no later than the stored row's `trading_day`; every symbol matches `^[A-Z][A-Z0-9.\-]{0,9}$`. Tests first in `test_state.py`, one per rule, including `"NaN"` and `"Infinity"`.
- [ ] T029 Small corrections: in `prices.py` fetch held symbols first (sorted), then new targets (sorted), so the deadline never starves the same held symbols (`service.py` passes the two groups); in `books.py` count `late_reports` only for reports actually applied (buy or hold entered, or a sell that lowered a weight), and class a holding as `exited_sell` only when a sell report set it to 0 today (a weight that merely rounds to 0 isn't a sell). Tests first in `test_prices.py` and `test_books.py`.
- [ ] T030 Role-limit test gaps, in `tests/integration/journal/test_role_limits.py`: add `decision_reports`, `execution_refusals`, `stop_loss_triggers` and `system_state` to the not-writable set; assert `DELETE` on `journal` is denied; run the checks as a login created in the test that is a member of `ta_journal` only (as `ta_journal_login` is), not only via `SET ROLE ta_journal`.
- [ ] T031 Quickstart step 3 (research J2, finding 6): run `--check` at the cron's time, 22:30 UTC, not just after the close, and treat `accepted: true` for liquid symbols as a release gate.
