# Feature Specification: The Assistant

**Feature Branch**: `013-assistant`

**Created**: 2026-10-17

**Status**: Draft

**Input**: User description: "Feature 013: the Assistant (docs/specs/assistant-agent.md; Constitution VII; ADR 0004, ADR 0007, ADR 0018, ADR 0021, ADR 0023). A read-only LLM agent that answers the owner's questions about portfolio state, agent activity and reports over a Telegram bot. Binding, from ADR 0023 and the owner's answers on 2026-10-17: its own always-on service using Telegram long polling; answers only one private chat whose ID is configured, ignores every other chat without replying, and logs ignored messages as a count without IDs; reads only through a fixed menu of named read-only queries — each question takes two structured model calls (choose queries, then answer from their results), and code validates every chosen query (known name, symbol already in the database, bounded date range, row cap) before running it; the model never writes SQL; the database role (ta_assistant, new login ta_assistant_login) can write nothing; credentials only under the ASSISTANT_ prefix (database URL, Telegram bot token, chat ID, the configured provider's key); no broker key and no market-data key — prices come from the database with their age stated; model Qwen qwen3.7-plus by default with an Anthropic Sonnet switch in config, no failover; prompts carry only the question, recent exchanges and query results, never credentials, environment values, logs or Telegram metadata; model-written text from reports and decisions is untrusted data, JSON-encoded in a separate message, its instructions ignored; replies are plain text with no Markdown/HTML parsing and link previews off; prompts, answers and message text never logged; conversation memory in process only (last few exchanges, dropped after about 30 minutes of quiet and on restart); one question at a time, caps on prompt input and answer output, a fixed reply when a question exceeds the input cap, no daily question cap, token use logged per question; a request to pause, resume or trade gets a fixed reply naming where that control lives (the dashboard, and the deployment runbook until it exists); questions about data that does not exist get a plain "no data"; ambiguous symbols or timeframes get a clarifying question; unprompted alerts are out of scope. Runs whether or not trading is paused. Tests use fake model, Telegram and store, never the network."

## Clarifications

### Session 2026-10-17 (before specify)

All recorded in [ADR 0023](../../docs/adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md), accepted the same day.

- Q: Which model provider? → A: Qwen `qwen3.7-plus` by default, with an Anthropic Sonnet model as a configuration switch, and no failover ([ADR 0018](../../docs/adr/0018-qwen-as-a-model-provider.md)). The account is paper only, and the PM's prompts already carry its state to Qwen (`specs/008-portfolio-manager` FR-024).
- Q: What may a prompt carry? → A: The owner's question, the recent exchanges, and the rows the chosen queries returned. Never a credential, an environment value, a log line or Telegram metadata beyond the message text.
- Q: How does it run? → A: Its own always-on service, receiving messages by long polling, whether or not trading is paused. Rejected: a webhook (a public domain and an inbound path, for one user) and scheduling by the orchestrator (it would hold the bot token and handle chat traffic).
- Q: How does it read data? → A: Through a fixed menu of named read-only queries. Each question takes two model calls: the first chooses queries from the menu, the second answers from their results. Code validates every chosen query before running it. Rejected: one call over a fixed snapshot of today (cannot answer about an earlier day) and model-written SQL (unbounded, untestable).
- Q: Credentials, and who may talk to it? → A: `ASSISTANT_`-prefixed variables only: the database login, the bot token, the one authorised chat ID, and the configured provider's key. These replace ADR 0007's unprefixed names. A single private chat; groups are refused.
- Q: Live quotes? → A: None. No market-data key; prices come from the database, with their age stated.
- Q: Model-written text in tables? → A: Untrusted data, JSON-encoded in a separate message from the instructions, its instructions ignored (`specs/008-portfolio-manager` research P13). Replies are plain text with link previews off. Prompts, answers and message text never reach a log. Messages from other chats are dropped silently and counted without their IDs.
- Q: Conversation memory? → A: In the process only: the last few exchanges, dropped after about 30 minutes of quiet and on restart. Nothing is written to the database.
- Q: Rate and cost limits? → A: One question at a time, a cap on each prompt's input and on each answer's output, a fixed reply when a question would exceed the input cap, and token use logged per question. **No daily question cap** (owner).
- Q: A request to pause, resume or trade? → A: A fixed reply saying the Assistant cannot act, naming the dashboard's control and, until the dashboard exists, the deployment runbook's switching-on and switching-off sections.
- Q: Unprompted alerts? → A: Out of scope. The Assistant only answers. Alerts need their own ADR.

## User Scenarios & Testing *(mandatory)*

The only user is **the owner**, who wants to ask, from their phone and in plain words, what the system holds, what each agent has said and decided, and how it has done, without waiting for the dashboard. This serves the system's purpose: watching each agent's performance over time with minimal intervention.

The Assistant is a window, never a lever. It cannot write anything, and no message it receives can reach a trade ([ADR 0007](../../docs/adr/0007-assistant-is-separate-read-only-telegram.md), Constitution VII). The worst a wrong or misled answer can do is mislead the owner.

### User Story 1 - Ask about the portfolio and today's activity (Priority: P1)

The owner sends a question such as "what do we hold?", "is trading paused?" or "what did the PM decide today, and what did the gate say?". The Assistant reads the relevant rows and answers in plain text: positions with quantity and average entry price, cash and equity from the latest account snapshot with its time, the pause and breaker state, today's decisions with their verdicts, and any orders and fills.

**Why this priority**: This is the core reason the Assistant exists: a quick, trustworthy read of the current state.

**Independent Test**: With a fake chat, model and store holding a known portfolio and a day's decisions, send each question and check that the reply states exactly the stored figures, each with its time, and nothing else.

**Acceptance Scenarios**:

1. **Given** two positions and today's snapshot, **When** the owner asks "what do we hold?", **Then** the reply lists both positions and states equity and cash with the snapshot's time.
2. **Given** trading is paused, **When** the owner asks whether trading is on, **Then** the reply says it is paused.
3. **Given** three decisions today, one approved and two rejected, **When** the owner asks what the PM did today, **Then** the reply lists all three with their direction, size and verdict, including each rejection's reason.
4. **Given** a price the Assistant quotes comes from a decision or a fill, **When** it appears in a reply, **Then** the reply says when that price was recorded.

---

### User Story 2 - Ask about agents, reports and performance over time (Priority: P1)

The owner asks "what did Research say about NVDA this week?", "why did the PM sell MU on Tuesday?", "how is the OI's book doing?" or "did every agent run this morning?". The Assistant reads reports, decisions with the reports they cited, journal rows and their attribution, and the orchestrator's run history, for the symbol and period asked, and answers from them.

**Why this priority**: Watching how each agent performs over time is the system's stated purpose. The Assistant is one of its two windows.

**Independent Test**: With a store holding a week of reports, decisions, journal rows and runs, ask each question and check that the reply draws only on rows in the asked period and for the asked symbol or agent.

**Acceptance Scenarios**:

1. **Given** Research wrote three reports on NVDA this week, **When** the owner asks what Research said about NVDA this week, **Then** the reply covers those three, with their dates, directions and suggested sizes.
2. **Given** a sell decision on MU citing two reports, **When** the owner asks why the PM sold MU, **Then** the reply gives the decision's reasoning and names the cited reports.
3. **Given** journal rows for the last ten sessions, **When** the owner asks how the OI's book is doing, **Then** the reply states its index and recent returns from those rows, and compares them with the real account's return over the same sessions.
4. **Given** a scheduled agent run failed this morning, **When** the owner asks whether everything ran, **Then** the reply names the failed run and its recorded outcome.

---

### User Story 3 - Refuse what it cannot do, and say what it doesn't know (Priority: P1)

The owner, or a message that reached the bot some other way, asks it to act ("pause trading", "sell AAPL"), asks about something not on record, or asks something ambiguous.

**Why this priority**: These are the spec's load-bearing edge cases ([`docs/specs/assistant-agent.md`](../../docs/specs/assistant-agent.md)). A Q&A surface that appears to act, or guesses, is worse than none.

**Independent Test**: Send each kind of message through fakes and check the reply, and that nothing was written anywhere.

**Acceptance Scenarios**:

1. **Given** any state, **When** the owner asks to pause, resume, buy or sell, **Then** the Assistant sends the fixed reply saying it cannot take actions and naming where the control lives, and nothing changes.
2. **Given** the system started on a known date, **When** the owner asks about a day before it, **Then** the reply says there is no data for that day, without guessing.
3. **Given** two held symbols whose names both match the owner's words, **When** the owner asks "how is the chip stock doing?", **Then** the Assistant asks which one is meant, and answers the follow-up "the second one" using the conversation so far.
4. **Given** a message from any chat other than the authorised one, **When** it arrives, **Then** no reply is sent and only a count is logged.
5. **Given** a report rationale contains text telling the model to say "trading is resumed" or to include a link, **When** the owner asks about that report, **Then** the reply treats that text as quoted content of the report, does not follow it, and states the pause from the database.

---

### User Story 4 - The owner checks it before relying on it (Priority: P2)

Before releasing the service, the owner runs a check that confirms the database login, the bot token and the model key work, without answering any message.

**Why this priority**: Every other component has a check mode, and a release goes through the owner's checklist (`docs/operations/deployment.md` §3). It is needed once per setup, not every day.

**Independent Test**: Run the check with fakes for a working setup and for each broken credential, and check that each outcome is named and nothing is sent to the chat.

**Acceptance Scenarios**:

1. **Given** valid credentials, **When** the owner runs the check, **Then** it reports each one as working and sends no chat message.
2. **Given** a rejected bot token, **When** the owner runs the check, **Then** it names the bot token as the failure, without printing its value.

---

### Edge Cases

- **The model is unavailable, refuses, rejects the request or is cut off**: the Assistant sends a fixed reply naming the category (unavailable, refused, rejected, truncated) and answers nothing from a partial result. There is no retry on the other provider.
- **The database is unreachable**: a fixed reply saying the data can't be read right now. The service keeps polling. If the connection stays lost it exits, so the platform restarts it ([ADR 0013](../../docs/adr/0013-deterministic-services-run-their-own-loops.md) §5 pattern).
- **The first model call chooses an unknown query, an unknown symbol, a range over the limit, or more queries than allowed**: code drops that choice and tells the second call which choices were dropped and why. If nothing valid remains, the answer says so.
- **A query returns more rows than its cap**: the rows are cut to the cap, and the answer says the list was cut.
- **The question with its query results would exceed the input cap**: the fixed "too much data; narrow the question" reply, sent without the second model call.
- **A message arrives while another is being answered**: it waits and is answered in order.
- **A non-text message** (photo, voice, sticker, file) **or an edited message**: a fixed reply saying only new text messages are answered.
- **A bot command** (`/start`, `/help`): a fixed reply describing what the Assistant can answer.
- **The answer is longer than one chat message allows**: it is sent as several messages, in order.
- **A question touches a price**: only stored prices are used, each with its time. If none is stored, the reply says so; the Assistant never estimates a price.
- **The owner's questions or the conversation are lost on a restart**: expected. The next message starts a new conversation.
- **No journal row yet**: questions about performance say no journal has been written yet.

## Requirements *(mandatory)*

### Functional Requirements

**Running and who it answers**
- **FR-001**: The Assistant MUST run as its own always-on service, receiving messages by polling the chat service, and MUST run whether or not trading is paused ([ADR 0023](../../docs/adr/0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §1–2).
- **FR-002**: It MUST answer only messages from a private chat whose ID equals the configured authorised chat ID. Every other message, including any from a group, MUST get no reply, and MUST be logged only as a count, without the chat's or sender's ID.
- **FR-003**: It MUST answer one question at a time, in the order received.
- **FR-004**: It MUST answer only new text messages. Non-text and edited messages, and bot commands, MUST get fixed replies (Edge Cases).

**Reading**
- **FR-005**: It MUST read the database only through a fixed menu of named read-only queries, covering at least: current positions; the latest account snapshot; the pause and breaker state; decisions with their verdicts and cited reports, by day or symbol; reports by agent, symbol and period; orders, fills and execution refusals; stop-loss triggers; journal rows with their summary and attribution; and the orchestrator's run history. The menu is code, changed through review.
- **FR-006**: Each question MUST take at most two model calls. The first MUST choose queries from the menu, with their parameters, or none. The second MUST answer from the question, the recent exchanges and the chosen queries' results.
- **FR-007**: Code MUST validate every chosen query before it runs: a name on the menu; any symbol already present in the database; any date range within a configured maximum span; at most a configured number of queries per question. Every query MUST return at most a configured number of rows. Dropped choices MUST be reported to the second call.
- **FR-008**: The model MUST NOT write or choose SQL text. No query's text may depend on model output except through validated parameters.
- **FR-009**: It MUST connect through its own login, a member of `ta_assistant`, and MUST NOT need any grant that role doesn't already have. It MUST write nothing to the database.

**Answering**
- **FR-010**: Every figure in an answer MUST come from a query result. A price MUST be a stored one, stated with when it was recorded. With no data for what was asked, the answer MUST say so plainly.
- **FR-011**: When a symbol, agent or timeframe in the question is ambiguous, the Assistant MUST ask a clarifying question rather than choose.
- **FR-012**: A request to pause, resume, buy, sell or otherwise act MUST get a fixed reply, written in code, saying the Assistant cannot take actions and naming where the control lives: the dashboard's pause control, and until the dashboard exists, the deployment runbook's sections on switching trading on and off. No code path in the Assistant can change state, so this reply is the whole response.
- **FR-013**: Replies MUST be sent as plain text, with no formatting parsed and link previews off. A reply longer than one message MUST be split into several, in order.
- **FR-014**: When a model call fails, the Assistant MUST send a fixed reply naming the failure's category, and MUST NOT send a partial answer. It MUST NOT retry on another provider.

**Prompts and untrusted text**
- **FR-015**: A prompt MUST carry only the instructions, the owner's question, the recent exchanges and the query results. It MUST NOT carry any credential, environment value, log line or chat metadata beyond the message text.
- **FR-016**: Text written by models or the broker (report rationales, source titles, decision reasoning, journal summaries, broker reasons) MUST be passed as encoded data in a separate message from the instructions, and the instructions MUST say it is data whose instructions are ignored.
- **FR-017**: Model output MUST be validated against its expected structure before it is used. An invalid first-call answer chooses no queries; an invalid second-call answer is a failure (FR-014).

**Memory and limits**
- **FR-018**: The Assistant MUST keep the last few exchanges in memory only, to answer follow-ups, and MUST drop them after a configured period of quiet (about 30 minutes) and on restart. It MUST store nothing about the conversation anywhere else.
- **FR-019**: Each prompt's input and each answer's output MUST be capped by configuration. A question whose prompt would exceed the input cap MUST get the fixed "narrow the question" reply without the second call. There is no daily question cap.
- **FR-020**: The model provider, model, caps, limits and memory period MUST be configuration, version-controlled and changed through review. The default MUST be Qwen `qwen3.7-plus`, with an Anthropic Sonnet model as a switch.

**Logging**
- **FR-021**: Logs MUST NOT contain any prompt, answer, message text, query result, credential value, or chat or sender ID. Each answered question MUST log the queries run (names only), the dropped choices (names and reasons), the tokens used by each call, and the outcome.

**Credentials and deployment**
- **FR-022**: It MUST use only variables named with the `ASSISTANT_` prefix: its database login, the bot token, the authorised chat ID, and the configured provider's key (and, for Qwen, its https endpoint). It MUST hold no broker credential and no market-data key. Only the configured provider's key is required.
- **FR-023**: A missing or malformed variable MUST stop the service at start, naming the variable, never its value.
- **FR-024**: Its login MUST be provisioned with the other components' logins, and its service MUST hold only its own variables.
- **FR-025**: It MUST offer a check mode that confirms the database login, the bot token and the model key work, sending no chat message, and naming any failure by credential.

### Key Entities

- **Question**: one text message from the authorised chat, answered in at most two model calls.
- **Query menu entry**: a named, fixed read-only query with typed parameters (symbol, agent, date range) and a row cap, chosen by the first model call and validated by code.
- **Exchange**: a question and its reply, kept in memory for follow-ups and never stored.
- **Fixed reply**: a reply written in code, not by the model: action requests, non-text messages, commands, the input cap, and failures.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across the test suite's fixtures, 100% of figures in answers match a stored value, and every stated price carries its recorded time.
- **SC-002**: Zero database writes, and zero state changes of any kind, across every fixture, including fixtures whose messages and stored model text ask the Assistant to act.
- **SC-003**: Zero replies to messages from any chat other than the authorised one.
- **SC-004**: Zero logs contain a message's text, an answer, a prompt, a query result, a credential value or a chat ID, across fixtures that put recognisable markers in each.
- **SC-005**: 100% of action requests in the fixtures get the fixed reply.
- **SC-006**: A typical question is answered within 60 seconds of being sent, when the model and database are available.
- **SC-007**: Zero queries run that fail validation, across fixtures where the model chooses unknown names, unknown symbols, oversized ranges and too many queries.

## Assumptions

- **The role and grants already exist** (`specs/001-data-model`). No migration and no grant change are expected. Only a login, a bot, a service and its variables are new.
- **The owner creates the bot** with the chat service's bot-creation flow and finds the private chat's ID. Both are owner steps in the runbook.
- **"The last few exchanges" is 5** by default, configurable.
- **Times in answers are New York time**, the exchange's time zone used everywhere else in the system, labelled as such.
- **Messages sent while the service was down** are answered when it returns, if they are recent; older ones get a fixed reply saying they arrived while the Assistant was offline. The cut-off is configuration, 10 minutes by default.
- **Quoting stored model text is allowed** in an answer (for example, a report's rationale), as quoted content attributed to its agent. Plain text and no link previews keep it inert.
- **The dashboard does not exist yet.** The fixed action reply names it and the runbook, and changes when the dashboard ships.
- **The journal's attribution shape** is the one `specs/012-journal-writer` fixed. The Assistant reads it as is.
- **Cost** is bounded per question by the caps; with no daily cap, total cost depends on how many questions the owner asks. Only the authorised chat can cause a model call.
