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

## 4. Owner-run live check (required once before deploying; read-only)

Touches only Finnhub with the owner's read-only key — never the broker, never the database. Run by the owner, not by an assistant or a test:

```bash
REFERENCE_DATA_FINNHUB_API_KEY=... .venv/bin/python -m trading_agent.reference --check AAPL MSFT BRK.B SPY
```

Confirm by eye:

- Market caps are in the trillions/billions of dollars (not ×10⁶ off) — validates D2's unit.
- Dollar volumes are plausible (billions for AAPL) and below market cap.
- `AAPL`/`MSFT` show `common_stock` on `XNAS`; `BRK.B` `common_stock` on `XNYS`; `SPY` `etf` (on `ARCX`, so the gate would reject it — expected).
- If a common stock shows `other`, note the provider's type string it printed; widening D3's mapping is a reviewed change.
- Each line prints the quote's `c`, `pc` and time `t`. Before the open, `t` should be either today (pre-market; `pc` is used) or the previous session (`c` is used). Check that the stored price matches the previous session's official close. If `t` shows an after-hours time, `c` may be an after-hours trade rather than the close: note it, and we decide whether to tighten D2.
- `currency` should be `USD` for US companies; a foreign issuer reporting in another currency fails as `non_usd_market_cap` (expected).

## Running

Its own process (ADR 0013), with only its own variables:

```bash
REFERENCE_DATA_FINNHUB_API_KEY=... REFERENCE_DATA_DATABASE_URL=... .venv/bin/python -m trading_agent.reference
```

No deployment config exists yet. Whichever feature first writes the Railway config must start this alongside `python -m trading_agent.execution` and `python -m trading_agent.risk`, each with only its own environment variables. The `ta_reference_data` role needs a login user created at deploy time (as for the other roles).

The `reference_candidate_symbols` view reads `reports` with its owner's rights, which relies on the migration admin owning `reports` (or being a superuser), since `reports` has row-level security enabled but not forced. On Railway, confirm the account behind `ADMIN_DATABASE_URL` owns the tables it created; if the view returned no report symbols, this is the first thing to check.
