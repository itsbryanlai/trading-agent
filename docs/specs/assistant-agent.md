# Assistant agent

## Purpose

Answers questions about portfolio state, agent activity, and reports, over a
single-user Telegram bot. See
[ADR 0007](../adr/0007-assistant-is-separate-read-only-telegram.md) for why
this is its own agent rather than folded into the orchestrator.

Not responsible for: placing, pausing, or influencing trades in any way —
see the Non-goals below, which are load-bearing, not incidental.

## Inputs

- A message from the authorized Telegram chat.
- Read access to every table in the shared knowledge base:
  `reports`, `decisions`, `risk_verdicts`, `orders`, `positions`, `journal`,
  `system_state`.

## Outputs

A Telegram reply. No database writes of any kind — its database role has no
write grants at all
([ADR 0004](../adr/0004-shared-postgres-role-scoped-credentials.md)).

## Edge cases

- **Message from an unauthorized chat ID**: ignore entirely; do not reply
  (a reply would confirm the bot exists to an unauthorized sender).
- **Question implies an action** ("pause trading", "sell my AAPL position"):
  explain that it can't take actions, and point to the dashboard's
  pause/resume control — never attempt to satisfy the request itself. This is
  a hard boundary, not a judgment call the model makes per-message.
- **Question about data that doesn't exist yet** (e.g. asking about a day
  before the system started): say so plainly rather than guessing.
- **Ambiguous symbol or timeframe**: ask a clarifying question rather than
  assuming — a wrong guess about *which* position the user means is worse
  than asking.

## Interfaces

- Reads all tables. Writes none.
- Telegram bot token and chat ID are its own credential, separate from every
  other credential in the system.

## Non-goals

- Cannot trigger, pause, resume, approve, or veto any trade.
- Cannot write to `system_state.trading_paused` even though it can read it —
  that toggle is exposed only through the dashboard's plain button
  ([ADR 0007](../adr/0007-assistant-is-separate-read-only-telegram.md)).
- Is not a second decision-maker in any sense — it has no path back into the
  trading pipeline at all.
