# Quickstart: Universe Reference-Data Job

How to validate feature 004 end to end. Contracts: [reference-data-interface.md](contracts/reference-data-interface.md), [market-data-port.md](contracts/market-data-port.md); schema: [data-model.md](data-model.md).

## Prerequisites

- The venv: `uv pip install --python .venv/bin/python -e ".[dev]"`
- For integration tests: Docker Desktop running and the `ta-pg` container on port 5433 (see the repo handover / `specs/003-execution/quickstart.md`).

## 1. Offline suite (no database, no network)

```bash
.venv/bin/python -m pytest tests/ -q
```

Expect the existing 271 plus the new `tests/unit/reference/` tests passing. They cover: normalization tables (D2–D5), the ticker check, symbol-set ordering, the schedule across weekends, holidays, early closes and the 08:00 / open / close boundaries, pacing and backoff against the fake provider, the adapter's status mapping and that the key never appears in URLs, logs or reprs, the config loader, and the import guard.

## 2. Integration suite (local Postgres)

```bash
TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres .venv/bin/python -m pytest tests/integration -m integration -q
```

New scenarios, all with the fake provider:

| Scenario | Expected |
|---|---|
| Pre-open tick with positions, reports, decisions, seeds | One row per fetchable symbol for today (US1) |
| Provider fails for a symbol that has yesterday's row | No row today; gate buy → `universe_no_reference_data` (US2) |
| Report inserted after the morning run | Row appears on the next tick (US3) |
| Second run / restart same day | No new calls for recorded symbols; rows unchanged |
| Weekend or holiday tick | No calls, no rows |
| `ta_reference_data` | Can select the view and insert rows; cannot update rows or read `positions`/`reports`/`decisions` (grants test, both ways) |
| Gate reads a job-written row | A buy in a qualifying symbol passes the universe rules |

## 3. Lint

```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

## 4. Owner-run live check (required before deploying; read-only)

Touches only Finnhub with the owner's read-only key — never the broker, never the database. Run by the owner, not by an assistant or a test. The key is typed into the terminal, never into a file or a chat:

```bash
read -s REFERENCE_DATA_FINNHUB_API_KEY && export REFERENCE_DATA_FINNHUB_API_KEY
```

```bash
.venv/bin/python -m trading_agent.reference --check AAPL MSFT BRK.B SPY
```

```bash
unset REFERENCE_DATA_FINNHUB_API_KEY
```

Each line prints the stored values, then what the provider sent: `type`, `mic`, `currency`, and the quote's `c`, `pc` and time `t`. The output never contains the key.

### Expected results (from the owner's run on 2026-09-29, during the session)

| Symbol | Result | What it confirms |
|---|---|---|
| AAPL | `common_stock XNAS`, market cap $4.94T, dollar volume $13.3bn/day, price = `pc` | Market cap is in millions (≈ price × shares outstanding); volume is in millions of shares; Finnhub sends `XNAS` directly (the tier-code mapping in D4 is a safety net) |
| MSFT | `common_stock XNAS`, $3.78T, $12.2bn/day | Same |
| BRK.B | `failed: share_class_unverified` | On that run, BRK.B's 10-day volume came back as about 270 shares a day: BRK.A's volume, not BRK.B's. Share-class tickers (`.` or `-`) now fail closed without any call (spec Clarifications 2026-09-30) |
| SPY | `failed: non_usd_market_cap` | ETFs have no company profile, so no currency. The gate would reject SPY anyway (ETF on `ARCX`), so this is expected |

Also check by eye:

- The stored price equals the **previous session's official close** (compare with any quote site).
- A common stock showing `other`: note the `type=` string it printed; widening D3's mapping is a reviewed change.
- A foreign issuer reporting in another currency fails as `non_usd_market_cap` (expected).

### Still to run: once before 09:30 ET on a trading day

The 2026-09-29 run was during the session, so every quote had rolled over (`t` today, price from `pc`). A pre-open run exercises the other branch of D2. Look at `t`:

- If `t` is today (pre-market), the price comes from `pc` and should be the previous close.
- If `t` is the previous session's day, the price comes from `c`. If `t` is an evening time (after 16:00 ET), `c` may be an after-hours trade rather than the official close. In that case, note the difference, and we decide whether to tighten D2.

## Running

Its own process (ADR 0013), with only its own variables:

```bash
REFERENCE_DATA_FINNHUB_API_KEY=... REFERENCE_DATA_DATABASE_URL=... .venv/bin/python -m trading_agent.reference
```

No deployment config exists yet. Whichever feature first writes the Railway config must start this alongside `python -m trading_agent.execution` and `python -m trading_agent.risk`, each with only its own environment variables. The `ta_reference_data` role needs a login user created at deploy time (as for the other roles).

The `reference_candidate_symbols` view reads `reports` with its owner's rights, which relies on the migration admin owning `reports` (or being a superuser), since `reports` has row-level security enabled but not forced. On Railway, confirm the account behind `ADMIN_DATABASE_URL` owns the tables it created; if the view returned no report symbols, this is the first thing to check.
