# Implementation Plan: Opportunistic Identifier agent

**Branch**: `011-opportunistic-identifier` | **Date**: 2026-10-07 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/011-opportunistic-identifier/spec.md`

## Summary

`python -m trading_agent.opportunistic_identifier` runs hourly, 10:00–15:00 ET, started by the orchestrator. Each run:
1. takes this run's slice of the owner's scan list, by a stateless rotation (research O3);
2. fetches each name's quote, profile and fundamentals from Finnhub, with its own read-only key (O2);
3. skips stale or incomplete names, and keeps those that pass the gate's universe rule on the reference-data job's own derivation (O4, O5);
4. leaves out names with an open OI report, ranks the rest by the average of two "fallen" ranks, and keeps 20 (O6);
5. makes one model call, to Qwen `qwen3.7-plus` by default (O7);
6. checks every proposal against the shortlist (O8);
7. writes buy reports with code-built sources, or one `no_action` row, in one transaction (O9, O12).

**How it's built**:
- **Pure core:** rotation, screening, ranking and answer checking are pure code, so "nothing off the shortlist is written" (SC-002) and even coverage (SC-003) are provable.
- **Two ports:** market data and the existing model client, each with a fake.
- **Migration 0014:** the orchestrator's newest-report view stops counting `no_action` rows (FR-023, O13).
- **Ships disabled:** the schedule entry gets its `env` list but stays `enabled: false`. A one-line PR enables it after the owner's dry run (O14).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: all existing. psycopg 3.2, PyYAML, exchange-calendars (via `risk.calendar`), the standard library's `urllib` for Finnhub and Qwen, and `anthropic` 1.x (only when switched). No new dependency.

**Storage**: the shared Postgres. The OI inserts `reports` as `ta_opportunistic_identifier`; the role and grants already exist. Migration 0014 replaces one view's definition. No new table or grant.

**Testing**: pytest with Hypothesis.
- **Offline:** fake market-data and model ports, and a fake opener for the adapter.
- **Integration:** the write, row-level security, the view and the logins.
- **No network:** `tests/conftest.py` blocks it.

**Target Platform**: a Linux child process of the orchestrator, on its Railway service (ADR 0015, 0021). No new service.

**Project Type**: an LLM agent in the existing `trading_agent` package; one run per process.

**Performance Goals**: fits the orchestrator's timeout for the OI, raised to 15 minutes (owner, after `/speckit-analyze`). The guarantee is a fetch deadline that leaves room for the configured provider's model attempts; the loader checks that a normally paced slice fits that window (O10). The default 40 names take about 369 s of a 650 s window on Qwen.

**Constraints**:
- **No portfolio, decisions, verdicts, orders or journal.** The only write is its own `reports` rows.
- **Only `OPPORTUNISTIC_IDENTIFIER_*` variables.**
- **Model output is checked before it's written.**
- **No automatic failover.**
- **Buy only.**

**Scale/Scope**:

| Item | Size |
|---|---|
| Runs | 6 a day |
| Fetched per run | up to 40 names (~123 Finnhub calls at 20 a minute, on a shared account); ETFs, OTC and share-class names cost no call |
| Sent to the model | 20 names, ~6k tokens in |
| Written | 0–20 reports out |
| Scan list | up to 1000 names |
| Full coverage | `ceil(ceil(U/40)/6)` trading days: a 240-name list is covered daily |

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | No broker credential, and no path to one. Only `reports` rows, which pass through the PM, the gate and Execution. Variables are `OPPORTUNISTIC_IDENTIFIER_*` only. The gate's code is not touched (O4). | Pass |
| II. Analysts propose, PM decides | Reports only, with an unbinding target weight. Sources are code-built (FR-013). It never reads decisions or the portfolio. It reads `config/risk.yaml`'s universe floors, which its behavior spec requires; the principle forbids that to the PM only (O4). | Pass |
| III. Least privilege at the database | No new grant. It uses the existing role and the `reports` row-level security policy. One new login for that role. An integration test asserts it can't read the trading tables. | Pass |
| IV. Autonomous, one hard stop | No approval gate added. Fewer PM runs, not more (FR-023). | Pass |
| V. Spec and ADR first | Covered by ADRs 0002, 0003, 0011, 0015, 0016, 0017 and 0018. The 0014 view change narrows an existing read within ADR 0011's wording (O13): no new role, flow or credential category, so no ADR. Updated alongside: `docs/specs/opportunistic-identifier-agent.md`, `orchestrator.md`, the overview row, and amendment notes in specs 005 and 010. | Pass |
| VI. Paper only, US equities | Strengthened: the OI pre-filters with the gate's own universe rule. The gate still re-checks every buy. | Pass |
| VII. Assistant and dashboard read-only | Not affected; the view's grants are unchanged. | Pass |
| Technology section (v1.1.1) | Qwen through the OpenAI-compatible API, and Anthropic through the SDK, in config. No new service (ADR 0021): the OI is a child of the orchestrator. | Pass |

**Re-check after design**: all pass. The design adds one config file, one view change and one login. It adds no table, no grant, no service and no variable outside `OPPORTUNISTIC_IDENTIFIER_*`.

## Project Structure

### Documentation (this feature)

```text
specs/011-opportunistic-identifier/
├── spec.md
├── plan.md                  # this file
├── research.md              # O1–O15
├── data-model.md            # reports rows, migration 0014, in-memory entities, fields sent
├── quickstart.md
├── contracts/
│   ├── oi-interface.md      # CLI, exit codes, env, config, answer schema, closed sets, logs
│   └── ports.md             # MarketData, ModelClient
├── checklists/requirements.md
└── tasks.md                 # /speckit-tasks
```

### Source code (repository root)

```text
src/trading_agent/
├── opportunistic_identifier/
│   ├── __init__.py
│   ├── __main__.py      # env, config, --dry-run, --check, exit codes (O12)
│   ├── config.py        # config/opportunistic_identifier.yaml loader, budget check (O10)
│   ├── ports.py         # MarketData, Listing, CompanyProfile, Fundamentals
│   ├── finnhub.py       # MarketData over urllib (O2)
│   ├── rotation.py      # pure: scan order, session index, slice (O3)
│   ├── screen.py        # pure: freshness, eligibility, ranking, shortlist (O4–O6)
│   ├── prompt.py        # PROMPT_VERSION, system prompt, user document (O7)
│   ├── answer.py        # pure: ANSWER_SCHEMA, check, drops, rows and sources (O8, O9)
│   ├── text.py          # pure: copy of research/text.py's clean (O8)
│   └── service.py       # one run: fetch with pacing and deadline, store protocol, Postgres store
├── storage/
│   ├── logins.py                                # + ta_opportunistic_identifier_login
│   └── migrations/0014_latest_argued_report.sql # O13

config/opportunistic_identifier.yaml   # new; scan_universe empty
config/schedule.yaml                   # env list filled in, timeout 15 minutes; stays enabled: false
pyproject.toml                         # opportunistic_identifier in the top layer
.env.example, .railway/railway.ts      # the five variables

tests/
├── fakes/oi_market_data.py
├── unit/opportunistic_identifier/     # one file per module, plus the gate-equivalence property and the import guard
├── unit/deploy/test_deployed_shape.py # pinned orchestrator variables
├── unit/storage/test_logins.py        # nine rows
└── integration/
    ├── opportunistic_identifier/      # write, all-or-nothing, row-level security, no trading-table access
    ├── orchestrator/                  # no_action doesn't move latest_report_time
    └── storage/test_logins.py

docs/specs/opportunistic-identifier-agent.md, docs/specs/orchestrator.md,
docs/architecture/overview.md, specs/005-orchestrator (note), specs/010-observe-only-deployment (note)
```

**Structure Decision**: a new top-layer package built like `research`. It reuses `reference.provider` and `reference.normalize` (O1, O4), `risk.calendar` and `risk.config`, and `llm`. It touches no other component's code except `storage/logins.py` (one row) and a view definition. `service.py` is the module most at risk of the 600-line limit; the fetch loop moves to its own `fetch.py` if it nears it.

## Things flagged for the owner

1. **The Finnhub account (owner, 2026-10-07: shared, OI slowed).** The OI shares one Finnhub account with the other components and paces at 20 calls a minute. The budget allows a `slice_size` of up to 71 on Qwen and 57 on Anthropic; the default is 40 (O10, O11).
2. **Ship disabled, enable separately (owner, 2026-10-07: agreed, O14).** This feature merges with `enabled: false`, so a release deploys nothing that runs. Enabling is a one-line PR after your `--check` and real dry run (quickstart steps 2–3) and once your scan list is filled in.
3. **The scan list ships empty.** You fill `config/opportunistic_identifier.yaml`'s `scan_universe`; I won't invent tickers. A list of up to ~240 names is covered every day at the defaults.
4. **The OI's timeout rises to 15 minutes** in `config/schedule.yaml` (owner, 2026-10-07, after `/speckit-analyze` B1). Allowed by the schedule's rules: the OI's timeout must be shorter than its 60-minute interval. Three orchestrator tests that assume 10 minutes are updated with it (T007a).
5. **The orchestrator's PM trigger changes (FR-023, migration 0014).** A view definition only. No grant, and no orchestrator code. It needs the usual open, migrate, close step at release.
6. **The universe check is a copy, not a shared function (owner, 2026-10-07: agreed, O4).** I've left `risk/gate.py` untouched. A property test pins the OI's copy to `gate._universe_stop`. The alternative, making the gate's function public, is a small refactor of Risk Gate code, which I'd rather not do inside this feature.
7. **The OI loads `config/risk.yaml`** with the gate's loader, for the universe floors only (O4). Not a limit change, and not a new reader of the limits that matter to the PM.
8. **Not yet confirmed, settled by your runs:**
   - the metric key names (step 2);
   - the token and cost estimate, ~$0.90 a month on Qwen at list price (step 3).
9. **Screening strategy (yours, recorded):** buy-the-dip ranking, with no news. The PM sees Research's news only for watchlist names.
10. **A late-started slot can repeat a batch** (research O3, accepted): rare and harmless.
11. **No change to risk limits, sizing or order logic.**

## Complexity Tracking

No constitution violations to justify.
