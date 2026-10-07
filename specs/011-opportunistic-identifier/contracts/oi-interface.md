# Contract: `python -m trading_agent.opportunistic_identifier`

## Invocation

| Command | Does |
|---|---|
| `python -m trading_agent.opportunistic_identifier` | One run: slice, fetch, screen, rank, call the model, check, write. Started by the orchestrator hourly, 10:00–15:00 ET, on trading days, or by hand. |
| `… --dry-run` | Everything except the write. Prints JSON lines: the `slice` (run index, batch of batches, symbols), each `skip` (symbol, reason), the `shortlist` (symbol, both ranks, score), each would-be row (`would_write`), each `dropped` proposal, and a `summary` with `RunCounts`, including `input_tokens` and `output_tokens`. No trading-window check. The database login is optional: without it, nothing counts as already open. |
| `… --check SYMBOL...` | Fetches the named symbols (at most 10) and prints, per symbol, the raw metric keys received, the derived values, and the skip reason or "eligible". No model call and no database. Used once before enabling, to confirm key names and units (quickstart step 2). |

Any other argument means exit 2.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Reports written; or a quiet `no_action` (`empty_scan_universe`, `empty_shortlist`, `nothing_argued`, `all_dropped`); or outside the window, with nothing done |
| 1 | A failure `no_action` was written; or the close passed during the run, so nothing could be written (`window_closed`, logged) |
| 2 | Refused to start: config, a missing or invalid variable, or an unknown argument |
| 3 | The database was unreachable, or the read or write failed |
| 4 | Crashed: an unexpected exception escaped, and no report could be written |

**Window**: a non-dry run does nothing (exit 0, logged) unless today is an XNYS session, the market is open, and it is at or after `slots.first` ET.

## Environment

| Variable | Required |
|---|---|
| `OPPORTUNISTIC_IDENTIFIER_DATABASE_URL` | Always, except for `--dry-run` (optional) and `--check` (unused) |
| `OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY` | Always |
| `OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY` | When `model.provider: qwen` (not for `--check`) |
| `OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL` | When `model.provider: qwen` (not for `--check`); `https://` only, named but never echoed if invalid |
| `OPPORTUNISTIC_IDENTIFIER_ANTHROPIC_API_KEY` | When `model.provider: anthropic` (not for `--check`) |

A missing required variable is reported by name, never by value, and the run exits 2. No other variable is read, including `ANTHROPIC_API_KEY`.

## Configuration: `config/opportunistic_identifier.yaml`

Every key is required, unknown keys are rejected, and any error means exit 2.

```yaml
scan_universe: []              # tickers; the owner fills this in. Sorted and de-duplicated at load.
slice_size: 40                 # names fetched per run, 1–200; the budget check caps it (45 at 20 calls a minute)
shortlist_size: 20             # names sent to the model, 1–40
quote_max_age_minutes: 15      # 1–60
finnhub_calls_per_minute: 20   # 1–60; 20 leaves room on a shared Finnhub account (research O11)
rationale_max_chars: 2000      # 200–10000
max_input_chars: 60000         # 5000–300000
slots:                         # must match config/schedule.yaml's opportunistic_identifier entry (tested)
  first: "10:00"
  every_minutes: 60
  count: 6
model:
  provider: qwen               # qwen | anthropic
  name: qwen3.7-plus
  max_output_tokens: 8000
  timeout_seconds: 120         # 30–300
  anthropic_effort: medium
```

**Loader rules**:
- Each `scan_universe` entry must pass `reference.symbols.is_plausible_ticker`, and must not be a share-class ticker (it can contain no `.` or `-`, since it could never pass eligibility). At most 1000 entries.
- `shortlist_size` must be ≤ `slice_size`.
- The budget check from research O10 must hold.
- `config/risk.yaml` must load with `risk.config.load_config`.

## The model's answer

One JSON object with exactly one key, `{"proposals": [...]}`. Each item has exactly these fields:

| Field | Type | Rule |
|---|---|---|
| `symbol` | string | exactly a symbol on this run's shortlist |
| `direction` | string | `buy` |
| `conviction` | integer | 1–5 |
| `suggested_size_pct` | number | above 0, at most 100 |
| `rationale` | string | cut to `rationale_max_chars` |

The JSON Schema is generated from this table in code (`answer.ANSWER_SCHEMA`).

## Closed sets

**Skip reasons** (per name, before the model):
- not fetched or not usable: `not_fetched`, `provider_unavailable`, `not_permitted`, `rate_limited`;
- quote: `stale_quote`, `missing_price`, `implausible_move`;
- fundamentals: `missing_52_week_high`, `missing_fundamentals`;
- every `reference.normalize` failure reason (for example `not_listed`, `share_class_unverified`, `non_usd_market_cap`, `missing_market_cap`, `missing_volume`, `implausible_market_cap`, `implausible_dollar_volume`, `value_out_of_range`);
- outside the universe: `universe_listing`, `universe_market_cap`, `universe_dollar_volume`, `universe_share_price`.

`already_open` is counted separately. It is left out before ranking, not skipped for data.

**Drop reasons** (per proposal): `malformed_answer`, `not_shortlisted`, `invalid_direction`, `invalid_conviction`, `invalid_size`, `duplicate_symbol`, `already_open`.

**Quiet `no_action` reasons** (exit 0): `empty_scan_universe`, `empty_shortlist`, `nothing_argued`, `all_dropped`.

**Failure categories** (`no_action`, exit 1): `symbol_list_unavailable`, `market_data_unavailable`, `input_too_large`, `model_key_rejected`, `model_rejected_request`, `model_unavailable`, `model_refused`, `model_truncated`, `unusable_answer`, `internal_error`.

## Logs

Logs never contain a variable's value, the prompt, or the model's answer.

| Level | When | Message |
|---|---|---|
| INFO | start | `opportunistic_identifier: run started (prompt v<v>, provider <p>, model <m>, batch <b>/<B>)` |
| INFO | fetch | `opportunistic_identifier: <n> in slice, <f> fetched, <s> skipped (<reason>: <count>, …), <o> already open` |
| INFO | shortlist | `opportunistic_identifier: <e> eligible, <k> shortlisted` |
| INFO | model | `opportunistic_identifier: model used <in> input and <out> output tokens` |
| INFO | check | `opportunistic_identifier: <r> proposals received, <a> accepted, <d> dropped` |
| INFO | per drop | `opportunistic_identifier: dropped proposal <i> (<symbol or ->): <reason>` |
| INFO | end | `opportunistic_identifier: wrote <n> report(s)` or `… wrote no_action (<why>)` |
| INFO | outside window | `opportunistic_identifier: outside the trading window; nothing to do` |
| ERROR | failure | `opportunistic_identifier: <category>: <exception type>`, plus ` (HTTP <status>)` when there is one |
| CRITICAL | exits 2 and 3 | the reason, by variable name or error type |
