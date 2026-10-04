# Contract: Market-Data Port

The only way the reference-data job talks to the market-data provider. One real implementation (`trading_agent.reference.finnhub`) and one fake (`tests/fakes/market_data.py`), both satisfying the `Protocol` in `trading_agent.reference.provider`. Decisions: [research.md](../research.md) D2, D6.

## Values

Frozen dataclasses. Numbers are `Decimal` exactly as the provider sent them (no unit conversion here; that is `normalize`'s job), or `None` when the field was absent.

| Value | Fields |
|---|---|
| `Listing` | `symbol`, `type` (provider string), `mic` (provider string), `conflicting` (the list named it more than once, differently; type and mic are then `None`) |
| `Profile` | `symbol`, `market_cap_millions`, `currency` (the currency market cap is reported in) |
| `Quote` | `symbol`, `current` (`c`), `previous_close` (`pc`), `timestamp` (`t`, when `current` was set, timezone-aware; `None` if absent or unusable) |
| `Metrics` | `symbol`, `avg_volume_10d_millions` |

## Calls

| Call | Returns | Raises |
|---|---|---|
| `list_us_symbols()` | `dict[str, Listing]` keyed by symbol; one request per exchange in `XASE`, `XNAS`, `XNYS`, merged, and any one failing fails the call (amended 2026-10-03: `exchange=US` alone is redirected) | `KeyRejected`, `RateLimited`, `ProviderUnavailable` |
| `get_profile(symbol)` | `Profile` (fields `None` if the provider returned `{}`) | `KeyRejected` (401), `NotPermitted` (403), `RateLimited`, `ProviderUnavailable` |
| `get_quote(symbol)` | `Quote` | as `get_profile` |
| `get_metrics(symbol)` | `Metrics` | as `get_profile` |

There is deliberately nothing that writes, and nothing that trades: the provider can't, and the port doesn't model it.

## Guarantees the real adapter gives

- The key travels only in the `X-Finnhub-Token` header; it never appears in a URL, log line, exception message or `repr`.
- 10-second timeout per call; no retries inside the adapter.
- Status mapping: 401 → `KeyRejected`; 403 → `KeyRejected` on the symbol list, `NotPermitted` on a per-symbol call (the plan doesn't cover that symbol; adversarial review M1); 429 → `RateLimited`; any other non-200, timeout, connection error, truncated or malformed response (`http.client.HTTPException`) or unparseable body → `ProviderUnavailable`.
- Strings and numbers are converted to `Decimal` at the boundary; a non-finite or unparseable number becomes `None` (then a failure in `normalize`), never zero.
- No provider type escapes the adapter.

## What the fake must model

Scripted listings, profiles, quotes and metrics per symbol; `{}`-style missing values; any error from any call for any symbol, including after N successes; `KeyRejected` at startup and mid-run; `RateLimited`; a call log with timestamps from an injected clock, so tests can assert pacing and that no symbol is fetched twice in a day.
