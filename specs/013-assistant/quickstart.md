# Quickstart: The Assistant

How to prove the feature works, from the tests to the first real question. Secret steps run in your own terminal tab, never through `!`. Export each value with zsh `read -rs "VAR?prompt"`.

## 1. Tests

The unit tests are offline, with fake Telegram, model and store:

```bash
.venv/bin/python -m pytest tests/unit/assistant tests/unit/deploy tests/unit/storage -q
```

Integration tests use a throwaway Postgres on 127.0.0.1:55432, never `ta-pg`:

```bash
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres .venv/bin/python -m pytest -m integration tests/integration/assistant tests/integration/storage -q
```

They check:
- every menu entry runs as `ta_assistant_login`;
- the login can't write any table;
- the eleventh login exists.

Then run the linter:

```bash
scripts/lint.sh
```

## 2. The bot and the chat (owner)

1. In Telegram, create a bot with @BotFather (`/newbot`). Store its token in your password manager.
2. Open a private chat with the bot and send it any message.
3. In your terminal tab, export the token with `read -rs`, then:

   ```bash
   PYTHONPATH=src .venv/bin/python -m trading_agent.assistant --find-chat-id
   ```

   Note the `chat.id` printed with `chat.type=private`. It is `ASSISTANT_TELEGRAM_CHAT_ID`. Do this before the service is deployed (research A3).

## 3. The login and the model key (owner)

1. Release the code first (`docs/operations/deployment.md` §3).
2. Open the database proxy, export `ADMIN_DATABASE_URL`, and run the logins command as for the journal (§8 step 1). It creates only `ta_assistant_login` and prints its string once.
3. Close the proxy and unset the variable.
4. For the model, use a DashScope key and endpoint for this agent (`ASSISTANT_DASHSCOPE_API_KEY`, `ASSISTANT_QWEN_BASE_URL`), or an Anthropic key with `model.provider: anthropic`.

## 4. Check (owner)

With every `ASSISTANT_*` value exported (`ASSISTANT_DATABASE_URL` through the proxy, with `sslmode=require`):

```bash
PYTHONPATH=src .venv/bin/python -m trading_agent.assistant --check
```

Every line must read `ok`: the bot token (`telegram`), no webhook on the bot (`telegram_webhook`), the database, both model calls (`model_plan` and `model_answer`), the time zone and the settings files. Nothing is sent to the chat. Any other result: don't deploy, and raise it.

## 5. Deploy

1. From a clean checkout of `release/prod`, after the guard test passes, run `railway config plan`. It must show one new service, `assistant`, always on, with only the `ASSISTANT_*` variables.
2. Run `railway config apply`.
3. Set the values in the Railway dashboard (service → Variables).

## 6. The first conversation (owner)

In the private chat, check each of these:
1. Send `/help`. The reply lists what it answers and the action words.
2. Ask "what do we hold?". Positions, equity and cash should match `docs/operations/observe-queries.sql`, with times in your zone and ET.
3. Ask "what did the PM decide today?". The decisions and their verdicts should be listed.
4. Send "pause trading", then "please pause trading". Both replies should start with "Nothing was done", and `system_state` should be unchanged. The logs should show `trigger=keyword` and `trigger=model_flag`.
5. Ask "what's the max position size?". It should say 8%, as deployed.
6. In the logs, `railway logs -s assistant --deployment --lines 20` should show one `answered` or `refused` line per message, with no message text.
