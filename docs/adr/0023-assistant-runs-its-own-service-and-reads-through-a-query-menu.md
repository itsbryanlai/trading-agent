# 0023. The Assistant runs as its own service, polls Telegram, and reads through a fixed query menu

Status: accepted

Accepted by the owner on 2026-10-17, before `/speckit-specify` for `specs/013-assistant`. The owner chose every decision below.

## Context

[0007](0007-assistant-is-separate-read-only-telegram.md) made the Assistant a separate LLM agent, read-only across every table, reachable over a Telegram bot scoped to one chat. `ta_assistant` and its read grants exist (`specs/001-data-model/contracts/role-grants.md`). Four things are undecided, and each changes the system's shape:

- **Where it runs.** A Telegram bot waits for messages, so it is always on. The orchestrator schedules LLM agents as one-run-per-process children ([0003](0003-orchestrator-is-a-scheduler-not-an-authority.md), [0015](0015-orchestrator-starts-agents-with-their-own-credentials.md)), which a chat bot does not fit. Each Railway service holds only its own credentials ([0021](0021-railway-deployment-as-code-observe-only-first.md)).
- **How it reads.** The shared model port makes one call with a structured-JSON answer: no tool use and no conversation. A question can need any table and any date.
- **Its credentials.** 0007 names `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Every other agent's variables carry its own prefix (0015, [0018](0018-qwen-as-a-model-provider.md)).
- **What its prompts carry.** 0018 left the Assistant's provider open. Its prompts would carry the owner's questions and portfolio state, and model-written text from `reports` and `decisions`.

## Decision

1. **Its own Railway service, `assistant`**, always on, one replica, started as `python -m trading_agent.assistant`. It holds only `ASSISTANT_*` variables, and Railway's default on-failure restart applies. It runs whether or not trading is paused. The orchestrator does not schedule or start it.
2. **Telegram long polling** (`getUpdates`), not a webhook. The service needs no public domain and accepts no inbound connection.
3. **One chat.** It answers only a private chat whose ID equals `ASSISTANT_TELEGRAM_CHAT_ID`. Any other chat, including a group, gets no reply, and is logged as a count without its ID.
4. **Reads go through a fixed menu of named read-only queries.** Each question takes two structured model calls: the first picks queries from the menu, the second writes the answer from their results. Code validates every chosen query before it runs: a known name, a symbol already in the database, a bounded date range, a row cap. The model never writes SQL. The database role stays the enforcement point (ADR 0004): the login is a member of `ta_assistant` and can write nothing.
5. **Credentials:** `ASSISTANT_DATABASE_URL` (a new login, `ta_assistant_login`), `ASSISTANT_TELEGRAM_BOT_TOKEN`, `ASSISTANT_TELEGRAM_CHAT_ID`, and the configured provider's key (`ASSISTANT_DASHSCOPE_API_KEY` with `ASSISTANT_QWEN_BASE_URL`, or `ASSISTANT_ANTHROPIC_API_KEY`). No broker key and no market-data key: prices in answers come from the database, with their age stated. These names replace 0007's unprefixed ones.
6. **The model:** Qwen `qwen3.7-plus` by default, with an Anthropic Sonnet model as a configuration switch, and no failover (0018). A prompt carries the owner's question, the recent exchanges, and the rows the chosen queries returned. It never carries a credential, an environment value, a log line or Telegram metadata beyond the message text.
7. **Model-written text is data.** Report rationales, decision reasoning and source titles go into the prompt JSON-encoded, in a separate message from the instructions, and instructions inside them are ignored ([`specs/008-portfolio-manager`](../../specs/008-portfolio-manager/research.md) P13). Replies are sent as plain text, with no Markdown or HTML parsing and link previews off. Prompts, answers and message text never reach a log.
8. **Conversation memory lives in the process only:** the last few exchanges, dropped after about 30 minutes of quiet and on restart. Nothing is written to the database.
9. **Limits:** one question at a time, a cap on each prompt's input and on the answer's output, and a fixed reply when a question would exceed the input cap. There is no daily question cap. Token use is logged per question.
10. **No actions and no alerts.** A request to pause, resume or trade gets a fixed reply naming where that control lives. Unprompted alerts are out of scope; adding them needs its own ADR.

## Alternatives considered

- **Scheduled by the orchestrator, polling Telegram every minute.** Rejected: the orchestrator would handle chat traffic and hold the bot token, and a minute's delay per reply buys nothing.
- **A webhook.** Not chosen: it needs a public domain, a secret check on every request, and an inbound path into the service, for one user.
- **One model call over a fixed snapshot of today's state.** Simpler and testable, but it cannot answer a question about an earlier day or an older report.
- **The model writes SQL, run as `ta_assistant`.** Rejected: the role makes it read-only, but queries would be unbounded in cost and size and could not be tested against fixed inputs.
- **Keep 0007's names, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.** Rejected: they break the prefix rule every other agent follows, which is what lets a guard test check a service's variables.
- **A daily question cap.** Not chosen by the owner.
- **Its own read-only Finnhub key for live quotes** ([0016](0016-market-data-for-the-llm-agents.md)). Not chosen: stored prices answer the questions the spec describes, and a key can be added later by its own decision.

## Consequences

- **A sixth worker service.** `.railway/railway.ts`, `tests/unit/deploy/test_deployed_shape.py` and `specs/010-observe-only-deployment/contracts/service-layout.md` gain an `assistant` entry holding only `ASSISTANT_*` variables. `storage/logins.py` gains `ta_assistant_login`. No migration and no grant change.
- **A new owner step:** creating the bot with Telegram's BotFather, and finding the private chat's ID.
- **A new outbound dependency:** the Telegram Bot API. The bot token can send messages as the bot and read its chat. It cannot reach the database, the broker or a model.
- **With Qwen, the owner's questions and the paper account's state reach Alibaba's servers**, as the PM's prompts already do (`specs/008-portfolio-manager` FR-024).
- **A wrong or injected answer can mislead the owner, but cannot act** (0007). Its worst outcomes are a wrong reply and, with no daily cap, model cost driven by the owner's own messages, since no other chat is answered.
- **The query menu bounds what it can answer.** A question no query covers gets a reply saying so; widening the menu is a code change, reviewed like any other.
- **No constitution amendment.** The Telegram bot token is already a named credential, and the deployment bullet already says one worker service per process.
