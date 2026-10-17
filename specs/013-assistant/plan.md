# Implementation Plan: The Assistant

**Branch**: `013-assistant` | **Date**: 2026-10-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/013-assistant/spec.md`

## Summary

`python -m trading_agent.assistant` runs always on, on its own `assistant` Railway service (ADR 0023). At start it skips every waiting message and tells the owner how many it skipped (A4). Then it long-polls Telegram and handles the authorised private chat's messages one at a time:
1. **Classify**, in code (A6):
   - an edited or non-text message gets a fixed reply;
   - `/commands` get the help text;
   - a message starting with an action word is refused, and the model explains the refusal after a fixed line (A7, A8, ADR 0024);
   - anything else is a question.
2. **Plan**: the first model call picks entries from a fixed query menu (A9).
3. **Validate and read**: code validates every choice (A10) and runs the valid ones as constant SQL, in one read-only transaction, as `ta_assistant_login` (A15). The `settings` entry reads an allowlist from the deployed config files (A16).
4. **Answer**: the second model call writes the reply from the results. Code removes any link that isn't a stored report source (A12), caps the reply and splits it into messages (A14).

**How it's built**: a pure core (`inbox`, `actions`, `menu`, `links`, `memory`, `replies`, `prompt`, `answer`), tested with no network or database. Thin edges handle Telegram, the store, settings files and the CLI. No migration and no grant change. One new login, one config file, one service and its variables.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: all existing:
- psycopg 3.2 and PyYAML;
- `llm` (ports, settings, `QwenClient`, `AnthropicClient`, and through it `anthropic`);
- the standard library's `urllib` through `no_redirect`, for Telegram (A1);
- `zoneinfo`.

No new dependency.

**Storage**: the shared Postgres, read-only as `ta_assistant` (data-model.md). No migration.

**Testing**: pytest with Hypothesis.
- **Offline:** a fake Telegram, the existing `FakeModel`, and a fake store. Property tests cover the action-word match, the link filter, the splitter and the menu validation. Fixtures with marker strings check the logs (SC-004).
- **Integration:** every menu entry runs as the real login against the real schema; the login can't write; the logins command has 11 rows.
- **No network:** `tests/conftest.py` blocks it.

**Target Platform**: Linux, a new always-on Railway service, one replica, Railway's default restart policy.

**Project Type**: a long-running bot process in the existing `trading_agent` package.

**Performance Goals**: a typical question answered within 60 s (SC-006). That is two model calls of at most 45 s each, but typical calls are seconds, plus a few bounded queries with a 10 s statement timeout.

**Constraints**:
- **Read-only**: no write, no broker key, no market-data key.
- **Only `ASSISTANT_*` variables.**
- **Prompts and replies stay out of logs.**
- **Replies**: plain text, previews off, links only from stored sources.
- **Model output is validated** before it's used.

**Scale/Scope**:

| Item | Size |
|---|---|
| Users | 1 chat |
| Questions | a few to tens a day, no cap (owner) |
| Model calls | 2 per question, 1 per refusal |
| Menu | 10 entries plus the known-symbol query |
| Rows per question | ≤ 4 queries × 50 rows |

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | No broker key and no write. The Assistant has no path to the gate or Execution. An action word is refused by code before any model call (ADR 0024 §1). | Pass |
| II. Analysts propose, PM decides | The Assistant decides nothing. Its output goes only to the owner's chat, and nothing it writes exists for the PM to read. | Pass |
| III. Least privilege at the database | No new grant. One new login for the existing read-only `ta_assistant`. Integration tests assert it can't write. Only `ASSISTANT_*` variables live on its service, and the guard test pins this. | Pass |
| IV. Autonomous, one hard stop | No approval gate added. The breaker is only reported. | Pass |
| V. Spec and ADR first | ADRs 0023 and 0024 cover the new service, credentials, data flows and the settings source, both accepted 2026-10-17. `docs/specs/assistant-agent.md`, the overview, the service-layout and logins contracts, and the runbook are updated, referencing them. | Pass |
| VI. Paper only, US equities | Not affected. | Pass |
| VII. Assistant and dashboard read-only | The point of the design. Read grants only, no write path in code, refusals by code, and a link filter that limits what an injected reply can do. | Pass |
| Technology section (v1.1.1) | Qwen or Anthropic per config (ADR 0018). The Telegram bot token is a named credential. One worker service per process, as code. | Pass |

**Re-check after design**: all pass. The design adds:
- one package and one config file;
- one login, one Railway service and its five possible variables;
- one menu entry reading config files (ADR 0024).

It adds no table, grant or migration.

## Project Structure

### Documentation (this feature)

```text
specs/013-assistant/
├── spec.md
├── plan.md                    # this file
├── research.md                # A1–A21
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── assistant-interface.md # CLI, env, config, exit codes, logs
│   ├── query-menu.md          # the entries, parameters, caps, settings allowlist
│   ├── model-io.md            # the three calls: documents, schemas, prompt rules
│   └── replies.md             # the fixed replies
├── checklists/requirements.md
└── tasks.md                   # /speckit-tasks
```

### Source code (repository root)

```text
src/trading_agent/
├── assistant/
│   ├── __init__.py
│   ├── __main__.py       # env, config, modes, exit codes, SIGTERM (A18)
│   ├── config.py         # config/assistant.yaml loader and bounds (A17)
│   ├── telegram.py       # getUpdates, sendMessage, getMe; the token never logged (A1, A2)
│   ├── inbox.py          # pure: authorised? which kind? the drain count (A4–A6)
│   ├── actions.py        # pure: the action-word match, the refusal document (A7, A8)
│   ├── menu.py           # pure: entry definitions and choice validation (A10)
│   ├── store.py          # the menu's SQL, one read-only transaction per question (A15)
│   ├── settings_view.py  # the allowlisted settings from config files (A16)
│   ├── prompt.py         # pure: PROMPT_VERSION, three system prompts, documents, the input cap (A9, A11, A14)
│   ├── answer.py         # pure: the two schemas, parsing and validating replies (FR-017)
│   ├── links.py          # pure: the link filter (A12)
│   ├── memory.py         # pure: the exchanges, with an injected clock (A13)
│   ├── replies.py        # pure: fixed reply texts and message splitting (A14)
│   ├── service.py        # one message: classify, refuse or plan → read → answer; the outcome
│   ├── loop.py           # the drain, the poll loop, backoff, stop (A2–A4)
│   └── check.py          # --check and --find-chat-id
├── storage/logins.py     # + ta_assistant_login

config/assistant.yaml                  # new; owner_time_zone: Asia/Singapore
pyproject.toml                         # assistant in the top layer
.env.example, .railway/railway.ts      # the variables; the assistant service

tests/
├── fakes/telegram.py, fakes/assistant_store.py
├── unit/assistant/                    # one file per module; log-marker fixtures; the import guard
├── unit/deploy/test_deployed_shape.py # the assistant service and its variables only
├── unit/storage/test_logins.py        # eleven rows
└── integration/
    ├── assistant/                     # every menu entry as the login; can't write
    └── storage/test_logins.py

docs/specs/assistant-agent.md, docs/architecture/overview.md, docs/operations/deployment.md (§9: adding
the assistant), .railway/README.md, specs/010-observe-only-deployment/contracts/{service-layout,logins-command}.md
```

**Structure Decision**: a new top-layer package. It imports `llm`, `storage.db` and `no_redirect`, and no sibling, `risk` or `reference` (A19). The only other component code it touches is one row in `storage/logins.py`. `service.py` and `store.py` are the modules most likely to grow: the menu's SQL stays in `store.py`, and the prompts in `prompt.py`.

## Things flagged for the owner

1. **`owner_time_zone: Asia/Singapore`** (owner, 2026-10-17). It is required in the config, with no code default.
2. **Polite action requests aren't caught by code** (A7). "Please pause trading" or "can you sell AAPL?" don't start with an action word, so they reach the model as questions. Its instructions say it can't act and must say so, and no code path can write either way. Adding "please" or "can" to the list would refuse every message starting with them.
3. **Default limits for you to adjust** (contracts/assistant-interface.md):
   - 4 queries a question, 50 rows a query;
   - a 92-day span;
   - 60,000 input characters, 2,000 output tokens, 12,000 reply characters;
   - 5 exchanges of memory, 30 minutes idle;
   - a 45 s model timeout.
4. **Deploys can briefly overlap**, so two pollers can conflict (A3). Telegram's 409 is retried, never fatal, and SIGTERM finishes the current message first.
5. **A message that crashes the process is skipped on restart** (A4): a side effect of skipping waiting messages, and protection against a restart loop.
6. **New credentials for you to create**:
   - the bot, with BotFather;
   - the chat ID, through `--find-chat-id`;
   - `ta_assistant_login`, through the logins command;
   - a model key for this agent.
7. **No migration**: the release is a merge to `release/prod` plus `railway config apply`. No proxy step except creating the login.
8. **Not touched**: every other component, every grant, `risk.yaml` and the pause.
