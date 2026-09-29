# Contract: Reference-Data Job Interface

How the job is run, what it needs, what it writes, and what it says. Decisions: [research.md](../research.md).

## Process

| Command | Needs | Does |
|---|---|---|
| `python -m trading_agent.reference` | `REFERENCE_DATA_FINNHUB_API_KEY`, `REFERENCE_DATA_DATABASE_URL` (a `ta_reference_data` login), `config/reference_data.yaml` | The loop: startup checks (D13), then a tick every 60 s (D7, D8) |
| `python -m trading_agent.reference --check SYMBOL…` | `REFERENCE_DATA_FINNHUB_API_KEY` only | Fetches and normalizes the given symbols, prints one line each (row values or failure reason), writes nothing, exits (D12) |

No other environment variable is read. In particular no `ALPACA_*` and no other component's `*_DATABASE_URL` (FR-020).

## Exit codes

Same as Execution and the gate's runner.

| Code | Meaning |
|---|---|
| 0 | Clean stop (tests: `max_ticks` reached) |
| 2 | Refused to start: missing variable, bad config file, key rejected or key check incomplete, another instance holds the lock |
| 3 | Database unreachable at start, or connection lost while running |

Provider errors after startup never exit the process.

## Configuration: `config/reference_data.yaml`

| Key | Type | Rule |
|---|---|---|
| `seed_symbols` | list of strings | Each must pass the FR-003 ticker check; duplicates rejected; may be empty |
| `calls_per_minute` | integer | 1–300 |

Every key required; unknown keys rejected; a bad file refuses to start (exit 2). Changed only through code review (FR-002).

## Ticker check (FR-003)

`^[A-Z]{1,5}([.-][A-Z]{1,2})?$` — e.g. `AAPL`, `BRK.B`, `BF-B`. Anything else is skipped with reason `invalid_symbol` and never sent to the provider.

## Failure reasons

A closed set; each failed or skipped symbol is logged with exactly one.

| Reason | When |
|---|---|
| `invalid_symbol` | Fails the ticker check |
| `not_listed` | Absent from today's US symbol list |
| `missing_type` / `missing_mic` | Listed but the field is blank |
| `missing_market_cap` | Absent, zero or unparseable |
| `missing_price` | Previous close absent, zero, negative or unparseable |
| `missing_volume` | 10-day average volume absent, zero, negative or unparseable |
| `implausible_market_cap` | Computed market cap > $20 trillion (a unit error) |
| `implausible_dollar_volume` | Computed dollar volume > computed market cap |
| `value_out_of_range` | After rounding, a value doesn't fit its column, or the price rounds to 0 |
| `provider_unavailable` | Timeout, network or server error for this symbol |
| `database_error` | The insert failed for a reason other than a lost connection |
| `rate_limited` | 429 on this symbol's call (the tick stops; retried next tick, not counted toward backoff) |

`KeyRejected` is not per symbol: one error line per tick (FR-019a). Every per-symbol reason except `rate_limited` starts that symbol's backoff (D8), so it is logged once per attempt, not every tick.

## Log lines

Formats are stable so the Assistant/dashboard features can parse them later.

| Level | When | Content |
|---|---|---|
| INFO | End of each tick that fetched anything | `reference: day=… candidates=N recorded=N already=N failed=N deferred=N` |
| WARNING | Each failed/skipped symbol | `reference: SYMBOL failed: <reason>` (next attempt time for backoff) |
| WARNING | First tick at/after the open (FR-025) | `reference: at open, no data for: SYM1, SYM2, …` |
| ERROR | Key rejected mid-run | `reference: market-data key rejected; skipping this run, retrying at …` |
| CRITICAL | Refusal to start / database lost | reason, never the key or connection string |
