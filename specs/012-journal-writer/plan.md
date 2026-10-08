# Implementation Plan: Journal writer

**Branch**: `012-journal-writer` | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/012-journal-writer/spec.md`

## Summary

`python -m trading_agent.journal` runs once on weekday evenings, started by Railway's cron on its own `journal` service ([ADR 0022](../../docs/adr/0022-journal-writer-runs-after-the-close-with-its-own-finnhub-key.md)). If today was a session and it has closed, the run:
1. reads the previous journal row, today's reports, decisions, verdicts, orders, refusals, triggers and account snapshots, in one read-only transaction;
2. fetches today's close for every symbol any book needs, from its own read-only Finnhub key, paced and bounded by a deadline (research J2, J8);
3. values each agent's book, drifts its weights, applies the reports, exits holdings past the 5-session limit, and scales above 100% (J5, J6);
4. counts each agent's PM usage (J9), and builds the summary from a fixed template with no model or broker text (J10);
5. upserts today's row (J11).

**How it's built**: a pure core (`books`, `usage`, `summary`, `state`) with no database or network, so the arithmetic (SC-002) and the "no agent text" property (SC-003) are tested directly. Thin edges handle prices, the store and the CLI. No migration, no grant, no model.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: all existing. psycopg 3.2, PyYAML, exchange-calendars (through `risk.calendar`), and `reference.finnhub` (the standard library's `urllib`). No new dependency.

**Storage**: the shared Postgres. Writes `journal` as `ta_journal`; reads already granted (data-model.md). No migration.

**Testing**: pytest with Hypothesis.
- **Offline:** a fake quote source and a fake store; property tests on the book arithmetic; fixtures for the summary.
- **Integration:** the upsert, the role's limits and the new login.
- **No network:** `tests/conftest.py` blocks it.

**Target Platform**: Linux, a Railway cron service (new, ADR 0022), `restartPolicyType: NEVER`.

**Project Type**: a deterministic batch job in the existing `trading_agent` package.

**Performance Goals**: under 10 minutes (SC-005): a 480 s fetch deadline plus database work.

**Constraints**:
- **No model call** and no broker credential.
- **Only `JOURNAL_*` variables.**
- **No model, broker or attribution text in `summary_md`.**
- **No grant change**, and the Risk Gate and Execution still can't read `journal`.

**Scale/Scope**:

| Item | Size |
|---|---|
| Runs | 1 a weekday (22:30 UTC) |
| Books | 2 agents today (`research`, `opportunistic_identifier`), any future agent automatically |
| Quotes per run | the symbols in any book plus today's targets: tens, at most ~160 within the deadline |
| Rows | 1 per session, ~250 a year, each a few kilobytes |

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | Nothing reaches the broker: no broker key, no write but `journal`. The Risk Gate and Execution still have no grant on `journal` (FR-020). `risk.calendar` is only imported, not changed (J6). | Pass |
| II. Analysts propose, PM decides | The journal decides nothing. Attribution never enters the PM's input: the summary carries none (spec, clarify Q1), and the PM already leaves `per_agent_attribution` out (specs/008 P6). | Pass |
| III. Least privilege at the database | No new grant. One new login, for the existing `ta_journal` role. Integration tests assert it can't write anything but `journal` and can't read `system_state`. | Pass |
| IV. Autonomous, one hard stop | No approval gate. The breaker is reported, not acted on. | Pass |
| V. Spec and ADR first | ADR 0022 covers the new service and credential. The behavior spec `docs/specs/journal.md` is added, and `docs/specs/data-model.md`, the overview and the service-layout and logins contracts are updated, all referencing ADR 0022. | Pass (ADR 0022 accepted 2026-10-09) |
| VI. Paper only, US equities | Not affected. Hypothetical books are measurement. | Pass |
| VII. Assistant and dashboard read-only | Not affected. They read the new rows through their existing grants. | Pass |
| Technology section (v1.1.1) | Finnhub is a named credential. One worker service per process, as code. No model. | Pass |

**Re-check after design**: all pass. The design adds one package, one config file, one login, one Railway service and two variables. It adds no table, grant, migration or model call.

## Project Structure

### Documentation (this feature)

```text
specs/012-journal-writer/
├── spec.md
├── plan.md                  # this file
├── research.md              # J1–J14
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── journal-interface.md # CLI, env, config, exit codes, logs
│   ├── attribution.md       # per_agent_attribution, schema_version 1
│   └── summary-template.md  # summary_md, SUMMARY_VERSION 0.1
├── checklists/requirements.md
└── tasks.md                 # /speckit-tasks
```

### Source code (repository root)

```text
src/trading_agent/
├── journal/
│   ├── __init__.py
│   ├── __main__.py      # env, config, --dry-run, --check, exit codes (J12)
│   ├── config.py        # config/journal.yaml loader and bounds (J14)
│   ├── model.py         # Holding, Book, ReportRow, Price, DayFacts, RunOutcome
│   ├── books.py         # pure: value, drift, apply, holding limit, scale, round (J5, J6)
│   ├── usage.py         # pure: per-agent usage counts (J9)
│   ├── summary.py       # pure: SUMMARY_VERSION, the template, the length bound (J10)
│   ├── state.py         # pure: attribution object to and from books, schema_version (J11)
│   ├── prices.py        # quotes through reference.finnhub, pacing, retries, deadline, J2's rule (J8)
│   ├── store.py         # Postgres reads in one read-only transaction, the upsert (J11)
│   ├── check.py         # --check output
│   └── service.py       # one run: the J3 gate, the order of steps, the outcome
├── storage/logins.py    # + ta_journal_login

config/journal.yaml                    # new
pyproject.toml                         # journal in the top layer
.env.example, .railway/railway.ts      # the two variables; the cron service

tests/
├── fakes/journal_quotes.py
├── unit/journal/                      # one file per module; a multi-session fixture; the import guard
├── unit/deploy/test_deployed_shape.py # the journal service, cron and restart policy
├── unit/storage/test_logins.py        # ten rows
└── integration/
    ├── journal/                       # upsert and re-run, role limits, reads against real rows
    └── storage/test_logins.py

docs/specs/journal.md (new), docs/specs/data-model.md, docs/architecture/overview.md,
docs/policy/versioning.md, docs/operations/deployment.md, .railway/README.md,
specs/010-observe-only-deployment/contracts/{service-layout,logins-command}.md (notes)
```

**Structure Decision**: a new top-layer package. It imports `risk.calendar`, `reference.finnhub` and `reference.provider`, and `storage.db`, and no sibling. The only other component code it touches is one row in `storage/logins.py`. `service.py` is the module most likely to grow; reads and the write stay in `store.py` so it doesn't.

## Things flagged for the owner

1. **ADR 0022 is accepted (owner, 2026-10-09).** Its first open item is settled: the pinned Railway SDK declares `deploy.cronSchedule` and `restartPolicyType` (J1). The second, whether `/quote` after the close returns the close, can't be settled from documentation. It's guarded instead: a quote stamped after the close is refused (J2), and your `--check` after a close confirms it before release (quickstart step 3). The ADR records both.
2. **`equity_close` is the last snapshot Execution recorded (owner, 2026-10-09: accepted)**, normally from the last stop-loss window, up to about 30 minutes before the close (J7). The journal can't ask the broker. It's labelled "last recorded" in the summary, and its time is stored.
3. **A new Railway service, on cron**: `30 22 * * 1-5` (18:30 ET in summer, 17:30 in winter), restart policy `NEVER`. A failed run is re-run by hand the same evening (J1, FR-003).
4. **New credentials for you to create**: `ta_journal_login` (logins command) and `JOURNAL_FINNHUB_API_KEY` (a read-only Finnhub key, or the shared account's). Both are needed before the service runs.
5. **No migration**: the release is a merge to `release/prod` plus `railway config apply`. No proxy step.
6. **The holding limit and pacing are configuration** (`config/journal.yaml`): 5 sessions, 20 calls a minute, a 480 s deadline.
7. **Not touched**: `risk.calendar` (J6), the Risk Gate, Execution, the PM, and every grant.
