# Contract: Market-Data Port

The only way the reference-data job talks to the market-data provider. One real implementation (`trading_agent.reference.finnhub`) and one fake (`tests/fakes/market_data.py`), both satisfying the `Protocol` in `trading_agent.reference.provider`. Decisions: [research.md](../research.md) D2, D6.

## Values

Frozen dataclasses. Numbers are `Decimal` exactly as the provider sent them (no unit conversion here; that is `normalize`'s job), or `None` when the field was absent.

| Value | Fields |
|---|---|
| `Listing` | `symbol`, `type` (provider string), `mic` (provider string) |
| `Profile` | `symbol`, `market_cap_millions` |
| `Quote` | `symbol`, `previous_close` |
| `Metrics` | `symbol`, `avg_volume_10d_millions` |

## Calls

| Call | Returns | Raises |
|---|---|---|
| `list_us_symbols()` | `dict[str, Listing]` keyed by symbol | `KeyRejected`, `RateLimited`, `ProviderUnavailable` |
| `get_profile(symbol)` | `Profile` (fields `None` if the provider returned `{}`) | same |
| `get_quote(symbol)` | `Quote` | same |
| `get_metrics(symbol)` | `Metrics` | same |

There is deliberately nothing that writes, and nothing that trades: the provider can't, and the port doesn't model it.

## Guarantees the real adapter gives

- The key travels only in the `X-Finnhub-Token` header; it never appears in a URL, log line, exception message or `repr`.
- 10-second timeout per call; no retries inside the adapter.
- Status mapping: 401/403 → `KeyRejected`; 429 → `RateLimited`; any other non-200, timeout, connection error or unparseable body → `ProviderUnavailable`.
- Strings and numbers are converted to `Decimal` at the boundary; a non-finite or unparseable number becomes `None` (then a failure in `normalize`), never zero.
- No provider type escapes the adapter.

## What the fake must model

Scripted listings, profiles, quotes and metrics per symbol; `{}`-style missing values; any error from any call for any symbol, including after N successes; `KeyRejected` at startup and mid-run; `RateLimited`; a call log with timestamps from an injected clock, so tests can assert pacing and that no symbol is fetched twice in a day.
