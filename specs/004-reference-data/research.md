# Research: Universe Reference-Data Job

Decisions behind [plan.md](plan.md), numbered D1–D14 so tasks, code comments and reviews can cite them. Each gives the decision, why, and what else was considered.

## D1. A pure core, a provider port, and a thin service

**Decision**: Same shape as Execution (`specs/003-execution` research E1).

- `trading_agent.reference.normalize`: pure. It takes one symbol's raw provider values and returns either a `ReferenceRow` or a `Failure(reason)`. It does all unit conversion, type and exchange mapping, and sanity checks (FR-005, FR-007–FR-010).
- `trading_agent.reference.schedule`: pure. Given `now`, it says whether fetching is allowed, and whether the open-time warning is due.
- `trading_agent.reference.symbols`: pure. It builds the day's ordered symbol set from candidate rows plus the seed list, and filters out implausible tickers (FR-001, FR-003).
- `trading_agent.reference.provider`: the `MarketDataProvider` protocol and its errors. `trading_agent.reference.finnhub` is the one real adapter; `tests/fakes/market_data.py` is the fake.
- `trading_agent.reference.service`: one pass. It reads the candidate view, decides what to fetch, calls the provider through the pacer, inserts rows and logs. `__main__` is the loop.

**Why**: the fail-closed rules (FR-005–FR-010) are where a wrong row would let a bad buy through. As pure functions they get table and property tests with no database and no network.

**Alternatives**: one module doing everything, which is harder to test exhaustively.

## D2. Finnhub endpoints and units

**Decision**: four free endpoints, all read-only. Sources: Finnhub's machine-readable API description (`https://finnhub.io/static/swagger.json`), read on 2026-09-29. No call was made.

| Field | Endpoint | Conversion |
|---|---|---|
| Security type, exchange MIC | `GET /stock/symbol?exchange=US` (the whole US list in one call, cached for the trading day) | `type` is mapped per D3; `mic` per D4 |
| Market cap | `GET /stock/profile2?symbol=` → `marketCapitalization` | × 1,000,000 (millions of USD, per the sample: AAPL 1415993) |
| Previous close | `GET /quote?symbol=` → `pc`, `c` and `t` (see below) | USD as is |
| 10-day average volume | `GET /stock/metric?symbol=&metric=all` → `metric.10DayAverageTradingVolume` | × 1,000,000 shares (per the sample: AAPL 32.50147) |

- Average daily dollar volume = 10-day average volume in shares × previous close (spec Clarifications, FR-010).
- Share price = previous close (FR-004), chosen by the quote's time `t` (added after the adversarial review, H1):
  - if `t` is on today's trading day (pre-market or in session), the quote has rolled over, so use `pc`;
  - if `t` is on the previous session's day, it hasn't rolled yet, so use `c` (that session's last price; possibly an after-hours trade, which `--check` will show);
  - otherwise, or with no `t`, fail as `stale_quote`. A halted symbol's weeks-old quote can't pass the $5 floor.
- Market cap must be reported in USD (`profile2` → `currency`); anything else fails as `non_usd_market_cap` (review H2). A company reporting in yen would otherwise be stored as dollars, about 150× too big.
- `/stock/candle` (historical daily bars) is premium and is not used.

**Why**: this covers all five fields on the free tier with the same read-only key ADR 0010 names, so no new ADR is needed.

**Known weakness**: two units (the market cap and volume multipliers) are inferred from sample responses, not stated in the docs. The guards against a units mistake:
1. The plausibility check (dollar volume ≤ market cap, FR-010) catches a volume that is really in raw shares (×10⁶ too big).
2. An owner-run, read-only check (`--check`, D12) prints normalized values for a few well-known symbols before the job is trusted.

A market cap that is really in whole dollars would come out 10⁶ too big. The $20 trillion ceiling (D5, added after `/speckit-analyze` F5) rejects it automatically; the `--check` output is a second, human check.

## D3. Security-type mapping, fail closed

**Decision**: map the `/stock/symbol` `type` string, case-insensitively and exactly:

- `Common Stock` → `common_stock`
- `ETP`, `ETF` → `etf`
- `ADR` → `adr`
- anything else (REIT, Preferred, Unit, Right, Warrant, blank, …) → `other`

The docs don't list the full set of strings, so an unknown string becomes `other` and the gate's listing check rejects it.

**Why**: an uncertain type must never become `common_stock` (spec Edge Cases).

**Consequence**: if Finnhub labels REITs as `REIT`, they are excluded. That is conservative, and `config/risk.yaml` says "common stock". The `--check` run shows the real strings. Widening the mapping later is a reviewed code change.

## D4. Exchange-code mapping

**Decision**:
- Map the Nasdaq market-tier codes `XNGS`, `XNMS`, `XNCM` (and `XNAS` itself) to `XNAS`.
- Keep `XNYS` and `XASE` as they are.
- Record every other code unchanged (for example `ARCX`, `BATS`, `OTCM`, `OOTC`), so the gate's `US_LISTED_MICS` check (`risk/rules.py`) rejects it (FR-008).

**Why**: Finnhub's sample shows AAPL as `XNGS`. Without the mapping, every Nasdaq stock would fail the listing check.

**Alternatives**: teaching the gate the tier codes. Rejected: the gate's rule stays keyed to operating exchanges, and normalization belongs to the writer.

## D5. Sanity checks (FR-005, FR-010)

**Decision**: a symbol gets a row only if all of these hold:
- the symbol is in today's cached US symbol list, with a non-empty type and MIC;
- the previous close (per D2) > 0, from a quote that isn't stale;
- the profile's `currency` is `USD`;
- the symbol isn't listed twice inconsistently (`conflicting_listing`);
- `marketCapitalization` > 0;
- `10DayAverageTradingVolume` > 0 (zero means no data, like market cap);
- every value parses as a finite `Decimal`;
- the computed market cap ≤ $20 trillion (`MAX_MARKET_CAP_USD`), about four times the largest company; above it is `implausible_market_cap`. This catches a market cap reported in whole dollars instead of millions (10⁶ too big), which otherwise passes every other check;
- the computed dollar volume ≤ the computed market cap;
- after rounding **down** to the column scale (so rounding never lifts a value over a gate floor; review), every value still fits its column (`numeric(20,2)`, `numeric(14,4)`) and is still > 0; otherwise `value_out_of_range`. Decimal overflow or an invalid quantize from extreme exponents is also `value_out_of_range`, never an exception (review M3).

Otherwise the result is `Failure(reason)`. The reasons are a closed set, listed in [contracts/reference-data-interface.md](contracts/reference-data-interface.md).

A zero market cap counts as missing. Finnhub returns `0` or `{}` for companies it doesn't know, so a zero means "no data", not a real value.

**Why**: fail closed. A missing row costs a skipped buy; a wrong row costs exposure.

## D6. The HTTP adapter: standard library, key in a header, no retries

**Decision**:
- `urllib.request` with a 10-second timeout, so there is no new dependency.
- The key goes in the `X-Finnhub-Token` header, never in the URL, so it can't leak through a logged URL or an exception message.
- The adapter's `repr` omits the key.
- The adapter never retries; retrying belongs to the service (D8).

HTTP outcomes map onto errors:

| Response | Error |
|---|---|
| 401, or 403 on the symbol list | `KeyRejected` |
| 403 on a per-symbol call | `NotPermitted`: that symbol's failure (`not_permitted`), backed off (review M1) |
| 429 | `RateLimited` |
| other 4xx or 5xx, timeout, network error, truncated or malformed response (`http.client.HTTPException`, converge T041), or unparseable JSON | `ProviderUnavailable` |
| 200 with an empty object | a missing value, handled by D5 |

**Alternatives**:
- `requests`, which is already installed via alpaca-py. Relying on another package's dependency is fragile, and declaring it adds a dependency for four GET calls.
- The official `finnhub-python` client. It puts the key in the query string and adds a dependency.

## D7. Schedule: a 60-second tick, fetching from 08:00 ET to the close

**Decision**: `python -m trading_agent.reference` ticks every 60 seconds (ADR 0013). On each tick, the pure `schedule` decides:

- **Not an XNYS session day** (`calendar.is_session`): do nothing (FR-015).
- **Before 08:00 ET on a session day, or at or after `calendar.close_time(day)`**: do nothing. Early closes are handled by the calendar.
- **Otherwise**: fetch. The "main run" is simply the first tick at or after 08:00. A process that starts late catches up on its first tick (FR-013), with no special mode.
- **At the first tick at or after `calendar.open_time(day)`**: log a warning naming every candidate symbol still without today's row (FR-025). This happens once per day and is held in memory, so a restart may repeat it. That is harmless.

**Why**: one rule covers the main run, catch-up and intraday pick-up. Every time judgement comes from `trading_agent.risk.calendar`, the same module the gate and Execution use (ADR 0013 §4).

## D8. Pacing, work per tick, order, and backoff

**Decision**:

- **Pacer**: calls are spaced at `60 / calls_per_minute` seconds. `calls_per_minute` comes from `config/reference_data.yaml`, default 30: half the commonly quoted free limit of 60 a minute, which is not confirmed. On `RateLimited`, the pass stops, and the next tick carries on after at least 60 seconds (FR-016).
- **Work per tick**: each tick spends at most about 50 seconds fetching, then returns. The next tick rebuilds the candidate set. A symbol named during a long morning run is therefore picked up within a tick or two (SC-003), instead of waiting for a 20-minute pass to finish.
- **Listing first**: `not_listed`, `conflicting_listing`, `missing_type` and `missing_mic` are decided from the cached symbol list before any per-symbol call, so a made-up ticker costs nothing (review M2).
- **Isolation**: an unexpected error while handling one symbol fails that symbol (`internal_error`, logged with a traceback); a database read error other than a lost connection skips the tick. Neither ends the process (review M3).
- **Order within a tick**, most likely to be bought first:
  1. symbols from reports still active or decisions made today;
  2. held positions;
  3. other recently named symbols;
  4. the seed list.
  
  Ties are broken alphabetically, so the order is deterministic.
- **Per-symbol backoff after a failure**: 5, 10, 20, then every 30 minutes (FR-014), for every per-symbol failure reason, including ones that cost no provider call (`invalid_symbol`, `not_listed`), so a failure is logged once per attempt rather than every tick. `rate_limited` is the exception: it doesn't count. It is held in memory, so a restart resets it. That only costs a few extra calls.
- **Key rejected**: the rest of that tick is skipped, one error is logged, and the next attempt waits 15 minutes (FR-019a).
- **Budget**: at 30 calls a minute and 3 calls per symbol, plus 1 bulk list call a day, 200 symbols take about 20 minutes. That is well inside 08:00–09:15 (SC-002).

**Alternatives**: one uninterrupted pass per trading day plus a separate intraday check. That is more states for the same result.

## D9. Idempotent insert, rows never changed

**Decision**:
- `INSERT … ON CONFLICT (symbol, trading_day) DO NOTHING`, one autocommit statement per row.
- A database error on one symbol's insert other than a lost connection (for example a CHECK violation that `normalize` should have prevented) is logged, counted as that symbol's failure (`database_error`) and backed off; it never exits the process. `OperationalError` still exits (FR-019).
- `trading_day` = `calendar.trading_day(now)` at the moment of the insert.
- Before fetching a symbol, the service checks whether today's row already exists, so no calls are wasted (FR-011, FR-018).
- The role has no UPDATE permission (D10), so a changed row is impossible, not just avoided.

## D10. Grants: a candidate-symbols view, and UPDATE revoked (migration 0009)

**Decision**: a new migration, `0009_reference_data.sql`:

1. `REVOKE UPDATE ON instrument_reference FROM ta_reference_data;`
2. `CREATE VIEW reference_candidate_symbols`, with columns `symbol`, `source` (`position` | `report` | `decision`), `named_at` (the latest `generated_at`; null for positions) and `active_until` (the latest `expires_at` for reports; otherwise null). It has no time filter (after `/speckit-analyze` F2–F3: a filter on the database clock would make fixed-date tests expire, and would drop a report still active after 10 days). It is grouped per symbol and source, so it grows with the number of distinct symbols, not reports. The view carries no text, reasoning, size or quantity.
3. `GRANT SELECT ON reference_candidate_symbols TO ta_reference_data, ta_assistant, ta_dashboard;`. The Assistant and dashboard can read everything (Constitution VII).

The view is owned by the migration admin and is not `security_invoker`, so it runs with the owner's rights. The job's role reads through it with no rights on `positions`, `reports` or `decisions`. The `reports` row-level security policy stays unchanged: RLS is enabled but not forced, so the table owner bypasses it. The exact FR-001 window (since the previous session's open) is applied in Python with the calendar, using a new `calendar.previous_session(day)` helper.

`specs/001-data-model/contracts/role-grants.md` and `tests/integration/storage/grants_matrix.py` are amended to match, and the both-ways grants test covers the view.

**Alternatives**:
- Column grants plus a change to the RLS policy (rejected in Clarifications).
- A `security_invoker` view. It would need the same table grants as that option.

**Not done here**: the handover's loose end, `ta_risk_gate`'s unused SELECT on `system_state_effective`. It is unrelated to this feature, and changes should stay small and focused.

## D11. Configuration: `config/reference_data.yaml`, strict

**Decision**: a new file, loaded with the same strictness as `risk.yaml`: every key required, unknown keys rejected, and a bad file means the process refuses to start.

```yaml
seed_symbols: []          # changed only through code review (FR-002)
calls_per_minute: 30      # provider pacing (D8)
```

It ships with an empty seed list. Choosing seed tickers is the owner's call; the spec forbids inventing them.

## D12. An owner-run, read-only check mode

**Decision**: `python -m trading_agent.reference --check SYMBOL [SYMBOL…]`. It:
- needs only the Finnhub key, and no database;
- fetches and normalizes the given symbols;
- prints each row or failure reason;
- writes nothing and exits.

The owner runs it once with their own key (quickstart), to confirm the units and type strings in D2 and D3 against live answers.

**Why**: the sample-derived units are this feature's biggest remaining uncertainty. The check costs a few lines and touches only the read-only data provider, never the broker. No test runs it; the suite's network guard would block it anyway.

## D13. Process, lock, and credentials

**Decision**:
- **Environment**: `REFERENCE_DATA_FINNHUB_API_KEY` and `REFERENCE_DATA_DATABASE_URL` only (FR-020). The key's name is scoped to this component (Constitution: each component's credential is distinct). Whether it is a different Finnhub account from Research's later key is the owner's choice. If they share an account, they share its rate limit, and `calls_per_minute` must leave room for Research.
- **Startup**:
  1. read both variables; if either is missing, exit with code 2;
  2. make one read-only provider call (the US symbol list, which is needed anyway). A `KeyRejected` means exit 2. An outage or rate limit doesn't: the check is deferred to the first tick, which fetches the list itself, because exiting would only restart the process into the same call (FR-019a as amended; review M4);
  3. connect with autocommit and keepalives, as Execution does;
  4. take a single-instance session advisory lock with its own key (`0x72656631`, "ref1"), distinct from Execution's single-instance key `0x65786531` and transaction key `0x65786563` and the gate's `0x7269736B`, waiting up to 5 minutes, then exit 2 (FR-017).

The symbol list fetched at startup is cached tagged with `calendar.trading_day(startup_now)`, and used only on that trading day.
- **Lost database connection**: exit 3 (FR-019).
- **Provider errors**: never exit the process.

**Why**: the same structure and exit codes as `trading_agent.execution` and `trading_agent.risk`, so deployment treats all three the same way.

## D14. Tests never touch the provider

**Decision**:
- `tests/fakes/market_data.py` scripts per-symbol values, errors and 429s, and records calls with timestamps so pacing can be asserted.
- The real adapter is tested through an injected URL opener that returns canned JSON copied from the swagger samples, plus error statuses. No socket is opened, and the suite-wide network guard in `tests/conftest.py` stays as it is.
- An import-guard test asserts that `trading_agent.reference` imports neither `alpaca` nor `trading_agent.execution`, and that no module in it reads `ALPACA_*` or any other component's `*_DATABASE_URL` (FR-020).
- The fetch path is mutation-checked, like earlier features.
