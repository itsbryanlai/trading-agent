# Contract: `python -m trading_agent.portfolio_manager`

## Invocation

| Command | Does |
|---|---|
| `python -m trading_agent.portfolio_manager` | One run: read, quote, call the model, check, write. Started by the orchestrator (morning session and event-driven runs, [ADR 0011](../../../docs/adr/0011-event-driven-portfolio-manager-runs.md)), or by hand. |
| `python -m trading_agent.portfolio_manager --dry-run` | Everything except the write, and without the market-hours check. Prints JSON lines: each symbol considered (`candidate`: symbol, quote, quote time, current weight, report ids), each skipped symbol (`skipped`: symbol, reason), each would-be row (`would_write`), each drop (`dropped`) and a `summary` (token use, input size). Needs the database (read only). |

Any other argument means exit 2.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Wrote decisions; or nothing to decide (no unexpired report, an empty answer, or every proposal dropped); or the market is closed, so nothing was done |
| 1 | A failed run, nothing written. Categories: `no_account_snapshot`, `quote_key_rejected`, `no_fresh_quotes`, `model_key_rejected`, `model_rejected_request`, `model_unavailable`, `model_refused`, `model_truncated`, `unusable_answer`, `window_closed`, `internal_error` |
| 2 | Refused to start: config, a missing variable, or an unknown argument |
| 3 | The database was unreachable, or a read or the write failed |
| 4 | Crashed: an exception escaped everything else |

## Environment

| Variable | Required |
|---|---|
| `PORTFOLIO_MANAGER_DATABASE_URL` | Always (the dry run reads too) |
| `PORTFOLIO_MANAGER_FINNHUB_API_KEY` | Always |
| `PORTFOLIO_MANAGER_DASHSCOPE_API_KEY` | When `model.provider: qwen` |
| `PORTFOLIO_MANAGER_QWEN_BASE_URL` | When `model.provider: qwen`: an `https://` URL matching the key's type. Anything else is exit 2, named but never echoed |
| `PORTFOLIO_MANAGER_ANTHROPIC_API_KEY` | When `model.provider: anthropic` |

A missing required variable is reported by name, never by value, then exit 2. The PM reads no other variable ([ADR 0015](../../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md)); the Anthropic client gets its key and base URL passed in explicitly, as Research's does.

## Configuration: `config/portfolio_manager.yaml`

Every key is required, unknown keys are rejected, and any error means exit 2. Bounds and cross-checks: [research P11](../research.md#p11-configuration-configportfolio_manageryaml).

```yaml
quote_max_age_minutes: 5          # a quote older than this when fetched is stale
finnhub_calls_per_minute: 30
quote_phase_seconds: 120          # stop fetching quotes after this long
journal_entries: 5                # recent journal days shown to the model
journal_summary_max_chars: 2000
rationale_max_chars: 2000         # each report's rationale, cut before the model sees it
reasoning_max_chars: 2000         # the PM's own reasoning, cut before it is written
max_input_chars: 300000
model:
  provider: qwen                  # qwen | anthropic
  name: qwen3.7-plus              # e.g. claude-sonnet-5-5 with provider: anthropic
  max_output_tokens: 8000
  timeout_seconds: 150
  anthropic_effort: medium        # low | medium | high
```

## The model's answer

One JSON object with exactly one key, `{"decisions": [...]}`; anything else is `unusable_answer`. Each item has exactly these fields:

| Field | Type | Rule |
|---|---|---|
| `symbol` | string | one of the symbols given in this run |
| `direction` | string | `buy`, `sell` or `hold` |
| `target_weight_pct` | number | 0–100; above 0 for a buy; buy above the current weight, sell below it; ignored for a hold |
| `reasoning` | string | cut to `reasoning_max_chars` |
| `report_ids` | array of strings | non-empty; each given in this run for this symbol; a buy cites a buy report; a conflicted symbol cites both sides |

The JSON Schema sent to the provider is generated in code (`answer.ANSWER_SCHEMA`), so the prompt and the checker can't drift apart.

## Drop reasons (closed set, logged per proposal)

`malformed_decision`, `unknown_symbol`, `invalid_direction`, `no_citation`, `unknown_citation`, `unbacked_buy`, `one_sided_conflict`, `invalid_size`, `direction_contradicts_target`, `duplicate_symbol`. Order and meaning: [research P8](../research.md#p8-the-answers-shape-and-its-checks).

## Skip reasons (per symbol, logged; the symbol isn't given to the model)

`quote_missing` (the fetch failed, or the phase deadline passed), `quote_stale` (research P4), `input_limit` (dropped to fit `max_input_chars`).

## Logs

[Research P14](../research.md#p14-logs). Never a variable's value, a prompt, an answer, a rationale or a reasoning.
