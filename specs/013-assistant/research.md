# Research: The Assistant

Decisions for `specs/013-assistant`. Each states the decision, why, and what else was considered. The owner's choices are in the spec's Clarifications and ADRs [0023](../../docs/adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) and [0024](../../docs/adr/0024-assistant-explains-refusals-and-reads-deployed-settings.md), refined by [0025](../../docs/adr/0025-assistant-model-flag-adds-refusals.md). Telegram facts are from the Bot API reference (https://core.telegram.org/bots/api, Bot API 10.3, read 2026-10-17).

## A1. Telegram through the standard library

**Decision**: a small client in `assistant/telegram.py`, with three methods: `getUpdates`, `sendMessage` and `getMe`. It makes one HTTPS POST per call, with a JSON body, through `trading_agent.no_redirect.open_without_redirects` (the same opener the Qwen client uses). No new dependency.

**Facts relied on**:
- `getUpdates` long-polls for `timeout` seconds (default 0, "for testing purposes only").
- "An update is considered confirmed as soon as getUpdates is called with an offset higher than its update_id."
- Updates are kept until received, but "not … longer than 24 hours".
- `allowed_updates` limits the update types delivered.
- `sendMessage` `text` is "1-4096 characters after entities parsing". `link_preview_options` takes a `LinkPreviewOptions` whose `is_disabled` turns previews off. `parse_mode` is optional. Without it and without `entities`, nothing is parsed as formatting.
- `getMe` is "a simple method for testing your bot's authentication token".
- Every response has a Boolean `ok`. Failures carry `description`, and sometimes `parameters.retry_after` for flood control.

**The token is in the URL** (`https://api.telegram.org/bot<token>/<method>`), so:
- no URL, request or exception text is ever logged, only the method name, the HTTP status and the exception type;
- `HTTPError` objects are never formatted, because they carry the URL;
- the client's `repr` hides the token, as `AnthropicClient`'s does;
- the token is checked against `^\d+:[A-Za-z0-9_-]+$` at start, and a malformed one refuses to start (exit 2), naming the variable. A stray `ValueError` or `http.client.InvalidURL` from the opener is mapped to `TelegramUnavailable` and never formatted (analyze M9).

**Alternatives**: `python-telegram-bot` or `aiogram`. Both are a large async dependency for three calls, and both log request URLs at debug level.

## A2. The poll loop

**Decision**:
1. `getUpdates(offset, timeout=50, allowed_updates=["message", "edited_message"])`, with an HTTP timeout of 60 s.
2. Updates are handled one at a time, in order, so FR-003 holds by construction.
3. The next poll's offset confirms everything handled. An update is confirmed once its reply has been sent or abandoned. Messages that arrive in the meantime wait on Telegram's side.

**Errors**:

| Response | Action |
|---|---|
| 401/404 at start | exit 2, naming `ASSISTANT_TELEGRAM_BOT_TOKEN` |
| 401/404 later | exit 2 |
| 409 (another poller, A3) | wait 10 s, then retry; log the count |
| 429 | wait `retry_after` (capped at 300 s), then retry |
| network failure, timeout or 5xx | back off 1, 2, 4 … 60 s, then retry; never exit |

**Sending** (analyze M10): each part of a reply is sent in order. A 429 waits `retry_after` (capped at 60 s) and retries that part once; a network failure or 5xx retries that part once after 2 s. If a part still fails, the remaining parts are abandoned, logged as `send abandoned at part k/n`, and the update is confirmed anyway: a part already sent is never sent again, and nothing is re-answered.

**Alternatives**: a webhook (rejected in ADR 0023). Threads per message: rejected by FR-003, and not needed for one user.

## A3. Deploys and a second poller

Railway starts the new deployment before it removes the old one. The pinned SDK, `railway@3.12.0`, declares `deploy.overlapSeconds` and `deploy.drainingSeconds`. For a few seconds two processes may therefore poll the same bot, and Telegram answers one of them with 409.

**Decision**:
- SIGTERM stops the loop after the current question is answered, then exits 0.
- A 409 is retried (A2). It is never fatal.
- `overlapSeconds` and `drainingSeconds` are left at Railway's defaults, so `railway.ts` stays the same shape as the other services.
- One replica, like every service (ADR 0021).

Telegram delivers each update to the poll that receives it, so a message is not answered twice. A message received by the old process while it shuts down is answered before it exits.

Each update is confirmed (`getUpdates` with the next offset) right after its reply is sent or abandoned, so a crash or a stop leaves no answered update unconfirmed. One window remains: if the old process has answered a message but its confirmation has not reached Telegram when the new deployment's poll starts, the new process may receive that update again and answer it twice. The duplicate costs the owner one repeated reply and changes nothing (the Assistant is read-only).

## A4. Messages from before a start (FR-004a)

**Decision**: at start, before the loop:
1. Drain the queue with `getUpdates(timeout=0, limit=100)`, confirming each batch, until it returns nothing.
2. Count the drained updates that are `message`s from the authorised private chat.
3. If the count is above 0, send the note from contracts/replies.md (R-SKIPPED) with the count.

Nothing drained is answered or logged except as counts.

**Side effect, intended**: a message whose handling crashes the process is skipped on restart, so one bad message can't cause a restart loop.

**Alternative**: `offset=-1`, which "forgets" earlier updates. Not chosen: it can't count them.

## A5. Who is answered (FR-002)

An update is handled only if all of these hold:
- it is a `message` or an `edited_message`;
- `chat.type == "private"`;
- `chat.id` equals `ASSISTANT_TELEGRAM_CHAT_ID`.

Everything else is dropped without a reply. Drops are logged as one line per poll batch, `assistant: ignored N update(s) from other chats`, with no ID.

In a private chat, the chat ID is the user's ID, so a separate sender check adds nothing. The chat ID is parsed as an integer at start, and a malformed value refuses to start.

## A6. Classifying an authorised message

This is pure code, in `inbox.py` and `actions.py`, applied in this order:

| Order | The message is | Outcome |
|---|---|---|
| 1 | an `edited_message`, a forwarded message (it has `forward_origin`), or one without `text` (photo, voice, sticker, file) | R-TEXT-ONLY. A forward carries a third party's words, so it is never a question (analyze L6) |
| 2 | text starting with `/` | `/start` and `/help` get R-HELP; any other command gets R-HELP too |
| 3 | text whose first word is an action word (A7) | the action refusal (A8) |
| 4 | anything else | a question (A9). If the plan call sets `action_request`, it becomes a `model_flag` refusal (A8) |

## A7. The action-word match (FR-012, ADR 0024 §1, ADR 0025 §3)

**Decision**: take the text and strip leading whitespace and punctuation. The first word is the first run of letters, compared case-insensitively with `action_words` from config. "Pause!", "SELL AAPL" and "  cancel the order" match. "Why did the PM sell MU?", "Is trading paused?", "Stop-loss triggers this week?" and "please pause trading" don't. The default list no longer has `stop` or `close` (ADR 0025 §3).

**Second layer (ADR 0025 §2)**: a polite form such as "please pause trading", "can you sell AAPL?" or "stop trading" is not caught by the keyword check. The plan call's `action_request` flag catches it (A9), and code sends the same refusal. Nothing could change either way, because no code path writes; the flag narrows the chance of a misleading reply.

## A8. The action refusal (FR-012, FR-012b, ADR 0024 §2, ADR 0025 §2)

Two triggers lead here: a keyword match (before any model call), or the plan call's `action_request: true` (before any query, including the known-symbol query).

1. Code builds the fixed first line, R-REFUSED-LINE.
2. One model call, with `REFUSAL_SYSTEM`, gets a JSON document holding only:
   - `message`: the owner's text;
   - `trigger`: `keyword` or `model_flag`;
   - `matched_word`: the word, or `""` for `model_flag`;
   - `action_words`: the list;
   - `controls`: the fixed list of where the controls live.

   No query runs. No history, setting value, query result or stored model text is included.
3. The model returns `{"reply": …}` (contracts/model-io.md). The sent text is the fixed line, a blank line, then the reply after the link filter (A12).
4. If the model call fails or the reply is invalid, code sends R-REFUSED-FALLBACK (keyword) or R-REFUSED-FALLBACK-FLAG (model flag): the fixed line, the list and the controls.

A flagged message therefore costs two model calls (plan, then refusal) and reads no data.

## A9. A question: two calls (FR-006, FR-007)

1. **Plan call** (`PLAN_SYSTEM`, schema `assistant_plan`). The document holds:
   - the question;
   - `now`, in the owner's zone and in ET, plus the owner's local date;
   - the recent exchanges (A13);
   - the query menu: names, what each returns and its parameters (contracts/query-menu.md).

   The model returns `{"action_request": bool, "queries": [{"name", "symbol", "agent", "from_date", "to_date", "section"}]}`. If `action_request` is true, the queries are ignored and the refusal (A8) follows. Every parameter is present as a plain string, with `""` for "not given", as strict JSON Schema needs. No nullable types: the other agents' schemas use none, so Qwen's strict mode is only relied on for plain types (analyze M11).
2. **Validation** in code (`menu.py`, A10), after the plan call: the known-symbol query runs only now, so a flagged message reads nothing. Valid choices run in one read-only transaction. Invalid ones are recorded with a reason.
3. **Answer call** (`ANSWER_SYSTEM`, schema `assistant_answer`). The document holds:
   - the question, `now` and the exchanges;
   - the results, each with its query, its parameters, its rows and a `cut` flag;
   - the dropped choices with their reasons.

   The model returns `{"reply": …}`.

   `ANSWER_SYSTEM` covers:
   - plain text only;
   - every figure from `results`;
   - every time in the owner's zone with ET alongside;
   - "no data" when there is none;
   - ask when a symbol, agent or period is ambiguous;
   - the Assistant can't act;
   - quoting stored text is allowed, as attributed content;
   - the untrusted fields (A11) are data.
4. If the plan answer is invalid, it is treated as `action_request: false` with no queries: no query runs and the answer call says nothing was looked up (FR-017). A polite action request whose plan answer is invalid therefore reaches the answer call; that is accepted residual risk, since no code path can write. An invalid answer reply gets R-MODEL-FAILED.

**Why two calls, not tool use**: the shared `ModelClient` port is one structured call (`llm/ports.py`). It works the same on both providers, and both calls are tested with `FakeModel`.

**Alternative**: one call with every query result attached. That would mean larger prompts and could still not answer about an earlier period.

## A10. Validating chosen queries (FR-007, FR-008)

Each choice is checked, and any failure drops it with a reason:

| Check | Reason when it fails |
|---|---|
| `name` is on the menu | `unknown_query` |
| only the parameters that query takes are non-empty | `unexpected_parameter` |
| every parameter the query requires is given (`universe` needs `symbol`) | `missing_parameter` |
| `symbol` matches `^[A-Z][A-Z0-9.\-]{0,9}$` and is in the known-symbol set: positions, reports, decisions, stop-loss triggers and `instrument_reference`, read by a fixed query after the plan call, only when `action_request` is false | `unknown_symbol` |
| `agent` is in the query's own list | `unknown_agent` |
| dates parse as ISO dates, `from_date` ≤ `to_date`, and the span is ≤ `max_span_days` | `bad_dates` |
| `section` is in the settings entry's list | `unknown_section` |
| it isn't the same as an earlier choice | `duplicate` |
| it isn't past the first `max_queries_per_question` choices | `too_many` |

Missing dates default per query (contracts/query-menu.md). Local dates become UTC instants with `zoneinfo`: from the start of `from_date` to the start of the day after `to_date`, in the owner's zone.

Every query is a constant SQL string with `%s` placeholders, ending in `LIMIT max_rows_per_query + 1`. One extra row is fetched so `cut` can be set. Model output reaches SQL only through validated, bound parameters.

## A11. Untrusted text in documents (FR-016)

Both model documents are JSON, sent as the user message after a fixed system prompt (the PM's P7 and P13 shape). These fields are model or broker text, each cut to `text_field_chars`:
- `reports.rationale_md`;
- `sources[].title` and `sources[].publisher`;
- `decisions.reasoning_md`;
- `journal.summary_md`;
- `orders.broker_reason`;
- `execution_refusals.details` (jsonb, serialised to compact JSON, then cut);
- `orchestrator_runs.detail`;
- earlier replies in the exchanges.

The system prompts name these fields and say their instructions are ignored.

The prompt only lowers the odds of a bad reply. The enforcement is that nothing the Assistant does can write (role, code), and the link filter (A12).

## A12. The link filter (FR-013a)

**Decision**: after every model reply, and before splitting:
- **Allowed**: the exact `url` strings in the `sources` of the report rows this question returned. They are protected first, and only where the match ends at whitespace, the end of the text, or one of `.,;:!?)]}"'` followed by whitespace or the end. So `https://source.example/a.evil.com` or `https://source.example/a@evil.com` is not protected and is filtered (analyze H3).
- **Replaced by `[link removed]`**:
  - any other `scheme://…` or `www.…` run;
  - any bare host name (`label.label…tld`, with a last label of 2 or more Unicode letters, or an `xn--` punycode label, case-insensitive), so IDN hosts such as `пример.рф` and `x.xn--p1ai` are caught;
  - `t.me/…`;
  - any `@username` (5–32 word characters).

  Telegram clients turn all of these into links even in plain text.

Tickers such as `BRK.B`, decimals, `U.S.` and `e.g.` don't match, because their last label has fewer than 2 letters or digits come first. A refusal reply has no allowed links, since its document has no reports. File names such as `deployment.md` do match (`.md` is a TLD), which is why no fixed reply or control name contains one (analyze H1).

**Alternative**: removing every link. The owner chose to keep source links (clarify Q4).

## A13. Memory (FR-018)

**Decision**: an in-process deque of the last `memory.exchanges` (5) pairs `{asked_at, question, reply}`. It is cleared when the newest is older than `memory.idle_minutes` (30), and lost on restart. Action refusals and fixed replies are not stored, so a refusal can't become part of a later question's context.

## A14. Limits (FR-019)

- **Input cap**: `max_input_chars`, on both documents. For each, the oldest exchanges are dropped first until it fits. If the answer document still doesn't fit with no exchanges, code sends R-TOO-MUCH and makes no answer call. The plan document always fits once exchanges are dropped, since one message is at most 4,096 characters (analyze L1).
- **Output**: `model.max_output_tokens` (2,000). A truncated reply gets R-MODEL-FAILED (`ModelTruncated`).
- **Reply length**: `max_reply_chars` (12,000) after the filter, cut with "…(cut)". It is then split into messages of at most 4,000 UTF-16 code units, at the last newline or space before the limit.
- **No daily cap** (owner).

## A15. The database (FR-009, ADR 0013 §5)

- A fresh connection per question, as `ta_assistant_login`, through the store's own `connect` callable, `psycopg.connect(url, connect_timeout=10, row_factory=dict_row, autocommit=False)`, injected for tests. `storage.db.connect` takes no timeout, and `storage/db.py` is not changed (analyze M3).
- One `REPEATABLE READ READ ONLY` transaction, so all of a question's results come from one snapshot, with `SET LOCAL statement_timeout` from config.
- A connection or query failure gets R-DATABASE.
- After `max_consecutive_failures` (3) failed questions in a row, the process exits 3 and Railway restarts it.

A long-lived connection would add reconnect logic for a few questions a day.

## A16. Settings (FR-005a, ADR 0024 §3)

**Decision**: `settings_view.py` reads the deployed files with `yaml.safe_load` and copies only the allowlisted paths in contracts/query-menu.md (`settings`). A missing file or key shows as `"unavailable"`. It doesn't import the other components' config loaders (siblings, forbidden by the layering) and doesn't validate. Validation stays with each owner, and the answer says the values are as deployed. `env`, `module` and every other key are never read into the result.

## A17. Configuration: `config/assistant.yaml`

Strict, like every other config: every key required, unknown keys refused (exit 2), changed only through code review. Defaults and bounds are in contracts/assistant-interface.md. The `model:` section is validated by `llm.settings.parse_model_settings`, with timeout bounds 10–120 s.

**`owner_time_zone: Asia/Singapore`** (owner, 2026-10-17, at plan review). It is required with no code default, and validated with `zoneinfo` at start; an unknown zone exits 2. The journal already uses `zoneinfo` in production.

## A18. Modes and exit codes

| Mode | Does | Needs |
|---|---|---|
| (none) | runs the service | every variable |
| `--check` | `getMe`; connects as the login and runs the known-symbol query; makes one minimal model call; loads the time zone and the settings files. Prints one line per check; sends nothing to the chat | every variable |
| `--find-chat-id` | polls for up to 120 s and prints the `chat.id` and `chat.type` of each message received; replies to nothing, and advances its offset as it reads so nothing is printed twice. That confirms those updates, which is harmless because it runs before the service is deployed | the bot token only |

`--find-chat-id` is for setup, while the service is not deployed, since a running service would take the updates (A3).

| Exit | Meaning |
|---|---|
| 0 | stopped by SIGTERM; `--check` all passed |
| 1 | `--check` found a failure |
| 2 | refused to start: arguments, config, a missing or malformed variable, a rejected bot token |
| 3 | the database failed `max_consecutive_failures` times in a row |
| 4 | crashed (never Python's default 1) |

## A19. Package and layering

**Decision**: a new top-layer package, `trading_agent.assistant`, added to the first layer of `pyproject.toml`'s import contract. It imports `llm` (ports, settings and both clients), `storage.db`, and `no_redirect`. It imports no sibling (`orchestrator`, `portfolio_manager`, `research`, `opportunistic_identifier`, `journal`, `execution`), and nothing from `risk` or `reference`. An import test pins this.

**Pure core**: `inbox`, `actions`, `menu` (validation), `links`, `memory`, `replies` (texts and splitting) and `prompt`. Each is tested without network or database.

## A20. Logs (FR-021)

The message format is in contracts/assistant-interface.md. No line contains a message's text, a reply, a prompt, a query result, a variable's value or a chat ID. Exceptions are logged by type only. A test runs fixtures with marker strings in each of those places and asserts that no log record contains them (SC-004).

## A21. Credentials and deployment

- **Login**: `ta_assistant_login`, the eleventh row in `storage/logins.py` and its contract. No grant change. An integration test asserts that the login can run every menu query and can't write to any table.
- **Variables**:
  - `ASSISTANT_DATABASE_URL`;
  - `ASSISTANT_TELEGRAM_BOT_TOKEN`;
  - `ASSISTANT_TELEGRAM_CHAT_ID`;
  - the provider's key: `ASSISTANT_DASHSCOPE_API_KEY` with `ASSISTANT_QWEN_BASE_URL` (https only), or `ASSISTANT_ANTHROPIC_API_KEY`.

  All of them go in `.env.example`, the service-layout contract and the guard test.
- **Railway**: a new `assistant` service in `.railway/railway.ts`, always on, one replica, Railway's default restart policy, with only `ASSISTANT_*` variables. The guard test learns that no other service holds an `ASSISTANT_*` name and that `assistant` holds nothing else.
- **No migration.**
