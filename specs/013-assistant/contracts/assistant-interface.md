# Contract: `python -m trading_agent.assistant`

An always-on process on the `assistant` Railway service (ADR 0023), or one owner-run mode (research A18).

## Invocation

| Form | Does | Needs |
|---|---|---|
| (no arguments) | skips the waiting messages (A4), then polls and answers until SIGTERM | every variable below |
| `--check` | checks the bot token, the database login, the model key, the time zone and the settings files; one line each; sends nothing to the chat | every variable |
| `--find-chat-id` | for up to 120 s, prints `chat.id` and `chat.type` for each message received; replies to nothing; advances its offset so nothing prints twice (run it before deploying) | `ASSISTANT_TELEGRAM_BOT_TOKEN` only |

Any other arguments exit 2.

## Environment

Only `ASSISTANT_*` names (ADR 0023 §5). A missing or malformed variable is named, never echoed (FR-023). Values are never logged.

| Variable | Use |
|---|---|
| `ASSISTANT_DATABASE_URL` | the `ta_assistant_login` connection string |
| `ASSISTANT_TELEGRAM_BOT_TOKEN` | the bot's token |
| `ASSISTANT_TELEGRAM_CHAT_ID` | the one authorised private chat, as an integer |
| `ASSISTANT_DASHSCOPE_API_KEY`, `ASSISTANT_QWEN_BASE_URL` | with `model.provider: qwen`; the endpoint must be https |
| `ASSISTANT_ANTHROPIC_API_KEY` | with `model.provider: anthropic` |

Only the configured provider's variables are required.

## Configuration: `config/assistant.yaml`

Every key is required, and unknown keys are refused (exit 2). It's changed only through code review.

| Key | Default | Bounds |
|---|---|---|
| `owner_time_zone` | `Asia/Singapore` (owner, 2026-10-17) | a zone `zoneinfo` can load; required, no code default |
| `model.provider` | `qwen` | `qwen` or `anthropic` |
| `model.name` | `qwen3.7-plus` | for `anthropic`, starts with `claude-` (e.g. `claude-sonnet-5-5`) |
| `model.max_output_tokens` | 2000 | 1000–64000 (`llm.settings`) |
| `model.timeout_seconds` | 45 | 10–120 |
| `model.anthropic_effort` | `low` | `low`, `medium` or `high` |
| `action_words` | `[pause, resume, unpause, stop, halt, buy, sell, close, cancel, trade, liquidate]` | 1–30 lowercase words, letters only, no duplicates |
| `memory.exchanges` | 5 | 0–20 |
| `memory.idle_minutes` | 30 | 1–240 |
| `limits.max_queries_per_question` | 4 | 1–10 |
| `limits.max_rows_per_query` | 50 | 1–500 |
| `limits.max_span_days` | 92 | 1–366 |
| `limits.max_input_chars` | 60000 | 5000–400000 |
| `limits.text_field_chars` | 1500 | 100–10000 |
| `limits.max_reply_chars` | 12000 | 500–40000 |
| `polling.long_poll_seconds` | 50 | 10–55 |
| `database.statement_timeout_seconds` | 10 | 1–60 |
| `database.max_consecutive_failures` | 3 | 1–20 |

## Exit codes

Research A18:
- 0: stopped by SIGTERM, or `--check` passed;
- 1: `--check` found a failure;
- 2: refused to start;
- 3: the database failed repeatedly;
- 4: crashed.

## Logs

One line per event, at INFO unless noted. The prefix is `assistant:`.
- **Start**: `assistant: start prompt_version=0.1 provider=<p> model=<m>`
- **Skipped at start**: `assistant: skipped N waiting message(s)`. Only if N > 0.
- **Ignored**: `assistant: ignored N update(s) from other chats`. One line per poll batch.
- **Fixed reply**: `assistant: replied fixed=<R-id>`
- **Refusal**: `assistant: refused prompt_version=<v> action word=<word> tokens=<in>/<out> outcome=explained|fallback`. The word is one from the configured list, never the message.
- **Question**: `assistant: answered prompt_version=<v> queries=<name,…|none> dropped=<name:reason,…|none> cut=<name,…|none> tokens=<plan in>/<out>,<answer in>/<out> links_removed=<n> messages=<n> outcome=<answered|too_much|model_failed:<category>|database>`
- **Send abandoned**: `assistant: send abandoned at part <k>/<n>` (WARNING).
- **Telegram**: `assistant: telegram <method> failed: <status or exception type>` (WARNING), with `retry in <s>s` where it applies.
- **Database**: `assistant: database failed: <exception type> (consecutive <n>/<max>)` (ERROR).
- **Stop**: `assistant: stopping (SIGTERM)`, or `assistant: exiting: <reason>` (CRITICAL).

No line contains a message's text, a reply, a prompt, a query result, a variable's value or a chat ID (FR-021). Railway labels these stderr lines "error"; read the text.
