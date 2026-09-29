# Implementation Plan: Universe Reference-Data Job

**Branch**: `004-reference-data` | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-reference-data/spec.md`

## Summary

A deterministic job, running in its own process (`python -m trading_agent.reference`, [ADR 0013](../../docs/adr/0013-deterministic-services-run-their-own-loops.md) "Consequences"), records one `instrument_reference` row per candidate symbol per XNYS trading day, so the Risk Gate's universe check can approve buys ([ADR 0010](../../docs/adr/0010-stop-loss-monitor-and-universe-reference-data.md) §3).

- **Candidates**: held positions, symbols from recent reports and decisions, and a seed list. They are read through a new symbols-only view (migration 0009, which also revokes the role's UPDATE).
- **Data source**: four free, read-only Finnhub endpoints ([research.md](research.md) D2). Dollar volume is the 10-day average volume × the previous close.
- **Core**: a pure normalizer maps types and exchanges, converts units and applies fail-closed sanity checks (D3–D5).
- **Loop**: a 60-second tick fetches from 08:00 ET to the close, at a controlled call rate, with the likeliest-to-be-bought symbols first and per-symbol backoff (D7–D8).
- **Tests**: no test touches Finnhub, and the owner runs a one-time read-only `--check` to confirm units (D12).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: psycopg 3.2, PyYAML, exchange-calendars (via `trading_agent.risk.calendar`), stdlib `urllib.request` for HTTP. No new dependency.

**Storage**: the shared Postgres. It writes `instrument_reference` and reads the new view `reference_candidate_symbols`.

**Testing**: pytest with hypothesis. Offline unit tests use the fake provider. Integration tests use the local `ta-pg` Postgres 16.

**Target Platform**: a Linux long-running process (Railway worker, not configured yet).

**Project Type**: a deterministic background service inside the existing `trading_agent` package.

**Performance Goals**: 200 symbols in about 20 minutes at 30 calls a minute (SC-002). A newly named symbol is recorded within 5 minutes (SC-003).

**Constraints**:
- Finnhub free tier: 30 calls a second is documented; the per-minute limit is unconfirmed, so the rate is configurable.
- Fail closed per symbol, and never carry rows forward.
- Credentials: only its own two environment variables.

**Scale/Scope**: tens to about 200 symbols a day; one row per symbol per day.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | The job is not in the order path. It is deterministic code with no model call. It holds no broker credential, and its import guard forbids `alpaca` and `trading_agent.execution`. The gate still reads the data itself rather than trusting upstream. | Pass |
| II. Analysts propose, PM decides | Not touched. The job reads only symbol names and never influences a decision's direction or size. | Pass |
| III. Least privilege at the database | Its own role, with inserts only into its output table. UPDATE is revoked. It reads a symbols-only view instead of the three base tables. Enforced by the both-ways grants test. | Pass |
| IV. One automatic hard stop | No new approval gate or stop. A failure only withholds buys in that symbol; exits never depend on it. | Pass |
| V. Spec and ADR first | The system's shape is covered by ADR 0010 §3 (new component, role, Finnhub key) and ADR 0013 (own loop, cited). A behavior spec for the new component, `docs/specs/reference-data.md`, is added, and `docs/specs/data-model.md` is updated for the view. No new ADR is needed. | Pass |
| VI. Paper only, US equities | Supports the gate's independent universe re-check. The mappings fail closed: unknown types become `other`, and exchange codes that don't map are kept as they are. | Pass |
| VII. Assistant and dashboard read-only | Both get SELECT on the new view. They have no new write access. | Pass |

Re-check after design: still all pass. The design adds one view and one config file, and no role gains a write.

## Project Structure

### Documentation (this feature)

```text
specs/004-reference-data/
├── spec.md
├── plan.md                      # this file
├── research.md                  # D1–D14
├── data-model.md                # table use, new view, grants delta
├── quickstart.md                # validation guide, owner-run --check
├── contracts/
│   ├── market-data-port.md      # provider protocol, adapter guarantees, fake
│   └── reference-data-interface.md  # process, env, exit codes, config, reasons, logs
├── checklists/requirements.md
└── tasks.md                     # /speckit-tasks
```

### Source Code (repository root)

```text
src/trading_agent/
├── reference/
│   ├── __init__.py
│   ├── __main__.py        # loop + --check; env, startup checks, exit codes (D12, D13)
│   ├── config.py          # config/reference_data.yaml loader, strict (D11)
│   ├── provider.py        # MarketDataProvider protocol, values, errors (contract)
│   ├── finnhub.py         # urllib adapter, header auth, status mapping (D6)
│   ├── normalize.py       # pure: raw → ReferenceRow | Failure (D2–D5)
│   ├── symbols.py         # pure: ticker check, window, ordering (D8, FR-001/003)
│   ├── schedule.py        # pure: fetch allowed / open warning due (D7)
│   └── service.py         # tick: view read, pacing, backoff, insert, logs (D8, D9)
└── storage/migrations/
    └── 0009_reference_data.sql   # view, grants, REVOKE UPDATE (D10)

config/
└── reference_data.yaml    # seed_symbols: [], calls_per_minute: 30

tests/
├── fakes/market_data.py
├── unit/reference/        # normalize, symbols, schedule, config, adapter, service
│                          #   pacing/backoff, main/exit codes, import guard
└── integration/
    ├── reference/         # ticks against Postgres + fake provider; gate reads rows
    └── storage/           # grants_matrix.py + role-grants.md amended; view tests

docs/specs/reference-data.md          # new behavior spec (Principle V)
docs/specs/data-model.md, README.md   # view + index entry
docs/architecture/overview.md         # the job's place, citing ADR 0010/0013
specs/001-data-model/contracts/role-grants.md  # amended by 004
.env.example                          # REFERENCE_DATA_* variables
```

**Structure Decision**: a new `trading_agent.reference` package beside `execution` and `risk`, mirroring Execution's split into a pure core, a port, a service and a main loop (D1). It reuses `trading_agent.risk.calendar` and `trading_agent.storage.db.require_env`, and imports nothing else from other components.

## Things flagged for the owner

- **No risk limit, position sizing or order logic changes.** The universe thresholds in `config/risk.yaml` are untouched. The job only provides the data they are checked against.
- **Dollar volume is an approximation** (10-day average × previous close), with units inferred from Finnhub's samples. The safeguards are automatic checks (dollar volume ≤ market cap, and a $20 trillion market-cap ceiling added after `/speckit-analyze`) and the owner-run `--check` (quickstart step 4), which stays required before deploying as a human check of the type labels and units.
- **The seed list ships empty.** Which tickers to seed is your call.
- **Finnhub account sharing.** If Research later uses the same Finnhub account, the two share its rate limit. `calls_per_minute` must leave room for Research.

## Complexity Tracking

No constitution violations to justify.
