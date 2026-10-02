# Contract: `python -m trading_agent.research`

## Invocation

| Command | Does |
|---|---|
| `python -m trading_agent.research` | One run: fetch, call the model, check, write. Started by the orchestrator at 08:30 ET on trading days, or by hand. |
| `python -m trading_agent.research --dry-run` | Everything except the write. Prints JSON lines: each article sent (`article`: id, time, headline, summary, tags), each would-be row (`would_write`), each drop (`dropped`) and a `summary` (research R10). No trading-window check. |

Any other argument means exit 2.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Wrote reports, or a "nothing to argue" or "all dropped" `no_action`; or outside the trading window, so nothing was done |
| 1 | Wrote a failure `no_action`; or the close passed while the run was going, so nothing could be written (`window_closed`, logged) |
| 2 | Refused to start: config, a missing variable, or an unknown argument |
| 3 | The database was unreachable, or the read or write failed |
| 4 | Crashed: an unexpected exception escaped, so no report could be written |

## Environment

| Variable | Required |
|---|---|
| `RESEARCH_DATABASE_URL` | Always, except for `--dry-run`, where it's optional (read-only) |
| `RESEARCH_FINNHUB_API_KEY` | Always |
| `RESEARCH_DASHSCOPE_API_KEY` | When `model.provider: qwen` |
| `RESEARCH_QWEN_BASE_URL` | When `model.provider: qwen`: the `https://` endpoint matching the key's type (Token Plan or pay-as-you-go). Anything else is exit 2, named but never echoed |
| `RESEARCH_ANTHROPIC_API_KEY` | When `model.provider: anthropic` |

A missing required variable is reported by name and never by value, then exit 2. Research reads no other variable. In particular, it never reads `ANTHROPIC_API_KEY`: the Anthropic client gets its key and `base_url` passed in explicitly, so the SDK's own environment lookup isn't used for either. The HTTP library may still honour standard proxy variables, which the orchestrator doesn't pass (ADR 0015's fixed base set).

## Configuration: `config/research.yaml`

Every key is required, unknown keys are rejected, and any error means exit 2. Bounds are in [research R11](../research.md#r11-configuration-configresearchyaml).

```yaml
watchlist: []                    # tickers; the owner fills this in
general_news_max_articles: 20
articles_per_symbol: 5
article_summary_max_chars: 1000
max_input_chars: 300000
rationale_max_chars: 2000
finnhub_calls_per_minute: 30
model:
  provider: qwen                 # qwen | anthropic
  name: qwen3.7-plus             # e.g. claude-sonnet-5-5 with provider: anthropic
  max_output_tokens: 8000
  timeout_seconds: 180
  anthropic_effort: medium       # low | medium | high
```

## The model's answer

One JSON object with exactly one key, `{"proposals": [...]}`; anything else is `unusable_answer`. Each item has exactly these fields, and nothing else:

| Field | Type | Rule |
|---|---|---|
| `symbol` | string | ticker check, and in the day's US symbol list |
| `direction` | string | `buy` or `sell` |
| `conviction` | integer | 1–5 |
| `suggested_size_pct` | number | buy: above 0, at most 100; sell: 0–100 |
| `rationale` | string | cut to `rationale_max_chars` |
| `article_ids` | array of strings | non-empty; each one given in this run; at least one about the company (tagged, from its company-news feed, naming the company, or `$SYM`, `(SYM)`, `EXCHANGE: SYM`) |

The JSON Schema sent to the provider is generated from this table in code (`answer.ANSWER_SCHEMA`), so the prompt and the checker can't drift apart.

## Drop reasons (closed set, logged per proposal)

`malformed_answer`, `invalid_symbol`, `unlisted_symbol`, `invalid_direction`, `invalid_conviction`, `invalid_size`, `no_citation`, `unknown_citation`, `uncited_symbol`, `duplicate_symbol`, `already_open`.

## Failure categories (written in the failure `no_action` row, exit 1)

`news_unavailable` (every news fetch failed, or the news key was rejected), `symbol_list_unavailable`, `model_key_rejected`, `model_rejected_request`, `model_unavailable`, `model_refused`, `model_truncated`, `unusable_answer`, `internal_error`.

## Logs (stdout or stderr; never a variable's value, an article's text or the model's answer)

| Level | When | Message |
|---|---|---|
| INFO | start | `research: run started (prompt v<N>, provider <p>, model <m>)` |
| INFO | news | `research: <k> articles in window, <n> sent (<t> tagged with a ticker; <c> chars, <d> dropped for size); missing: <feeds or none>` |
| INFO | model | `research: model used <in> input and <out> output tokens` |
| INFO | check | `research: <r> proposals received, <a> accepted, <d> dropped` |
| INFO | per drop | `research: dropped proposal <i> (<symbol or ->): <reason>` |
| INFO | end | `research: wrote <n> report(s)` or `research: wrote no_action (<why>)` |
| INFO | outside window | `research: not a trading session before the close; nothing to do` |
| ERROR | failure | `research: <category>: <exception type>`, plus ` (HTTP <status>)` for a model failure that had one; never the error's message |
| CRITICAL | exits 2 and 3 | the reason, by variable name or error type |
