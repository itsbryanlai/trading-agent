# Assistant agent

## Purpose

Answers questions about portfolio state, agent activity, and reports, over a
single-user Telegram bot. See
[ADR 0007](../adr/0007-assistant-is-separate-read-only-telegram.md) for why
this is its own agent rather than folded into the orchestrator.

Not responsible for: placing, pausing, or influencing trades in any way —
see the Non-goals below, which are load-bearing, not incidental.

## Inputs

- A message from the authorized Telegram chat, received by long polling
  (`getUpdates`), not a webhook ([ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md)).
- Read access to the shared knowledge base through a fixed menu of named,
  read-only queries (positions and account, decisions, reports, orders, stop
  losses, journal, agent runs, universe). The model picks entries from the
  menu and code validates and runs them; the model never writes SQL
  ([ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §4).
- A `settings` entry that reads a fixed list of non-secret values from the
  configuration files deployed with the service: risk limits, each agent's
  schedule and whether it is enabled, and each LLM agent's model. Variable
  names and credentials stay out. Answers describe what is deployed to
  `release/prod` and say so ([ADR 0024](../adr/0024-assistant-explains-refusals-and-reads-deployed-settings.md) §3, §4).

## Outputs

A Telegram reply, as plain text with link previews off. Times are given in
the owner's time zone and in ET. The only links a reply may carry are source
links stored in a report's sources; code removes every other URL
([ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §7). No database writes of any kind — its database role has no
write grants at all
([ADR 0004](../adr/0004-shared-postgres-role-scoped-credentials.md)).

## Edge cases

- **Message from an unauthorized chat ID**: ignore entirely; do not reply
  (a reply would confirm the bot exists to an unauthorized sender).
- **Question implies an action** ("pause trading", "sell my AAPL position"):
  explain that it can't take actions, and point to the dashboard's
  pause/resume control — never attempt to satisfy the request itself. This is
  a hard boundary, decided in code first: a message whose first word is on a
  configured list of action words is refused before any query or model call
  ([ADR 0024](../adr/0024-assistant-explains-refusals-and-reads-deployed-settings.md) §1). The reply opens with a fixed line saying nothing was done, then
  a model call explains why and what to do next, given only the message, the
  matched word, the word list and where the controls live ([ADR 0024](../adr/0024-assistant-explains-refusals-and-reads-deployed-settings.md) §2). As a
  second layer, the plan call also flags action requests the keywords miss
  ("please pause trading"); the flag can only add refusals, never remove one
  ([ADR 0025](../adr/0025-assistant-model-flag-adds-refusals.md) §2). The default list is pause, resume, unpause, halt, buy, sell,
  cancel, trade and liquidate; `stop` and `close` are left off because they
  begin ordinary questions ([ADR 0025](../adr/0025-assistant-model-flag-adds-refusals.md) §3).
- **Messages sent while the service was offline**: none is answered. On
  start it skips them all and sends one note saying how many were skipped and
  asking for them again ([ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §2, `specs/013-assistant` FR-004a).
- **Another chat (including a group)**: no reply, logged as a count without
  the ID ([ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §3).
- **Question about data that doesn't exist yet** (e.g. asking about a day
  before the system started): say so plainly rather than guessing.
- **Ambiguous symbol or timeframe**: ask a clarifying question rather than
  assuming — a wrong guess about *which* position the user means is worse
  than asking.

## Interfaces

- Reads through the query menu and the settings entry. Writes none.
- Its own service, `assistant`, always on with one replica; the orchestrator
  does not schedule it ([ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §1).
- Its credentials are its own and all carry the `ASSISTANT_` prefix, replacing
  the unprefixed names in ADR 0007 ([ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §5): `ASSISTANT_DATABASE_URL`
  (`ta_assistant_login`), `ASSISTANT_TELEGRAM_BOT_TOKEN`,
  `ASSISTANT_TELEGRAM_CHAT_ID`, and the provider's key
  (`ASSISTANT_DASHSCOPE_API_KEY` with `ASSISTANT_QWEN_BASE_URL`, or
  `ASSISTANT_ANTHROPIC_API_KEY`). No broker or market-data key.
- Model: Qwen by default, an Anthropic model by configuration, no failover
  ([ADR 0018](../adr/0018-qwen-as-a-model-provider.md), [ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §6).
- Conversation memory lives in the process only and is dropped after about 30
  minutes of quiet and on restart ([ADR 0023](../adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §8).

## Non-goals

- Cannot trigger, pause, resume, approve, or veto any trade.
- Cannot write to `system_state.trading_paused` even though it can read it —
  that toggle is exposed only through the dashboard's plain button
  ([ADR 0007](../adr/0007-assistant-is-separate-read-only-telegram.md)).
- Is not a second decision-maker in any sense — it has no path back into the
  trading pipeline at all.
