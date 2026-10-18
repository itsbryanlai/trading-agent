"""`python -m trading_agent.assistant`: the always-on Telegram assistant
(specs/013-assistant contracts/assistant-interface.md; research A18).

Reads its environment (names only are ever logged), loads config, builds the clients, then
drains the waiting messages and polls until SIGTERM. The database login is `ta_assistant_login`
and every read goes through the query menu (store.py).

Exit codes (research A18): 0 stopped by SIGTERM; 2 refused to start (arguments, config, a
missing or malformed variable, a rejected bot token); 3 the database failed repeatedly;
4 crashed, so an escaped exception is never Python's own exit 1.

Modes (check.py): `--check` makes one of each call and prints a line per check, exit 0 or 1;
`--find-chat-id` needs only the token and prints each message's chat id. Any other argument
exits 2.
"""

from __future__ import annotations

import logging
import re
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.assistant import check, loop, service
from trading_agent.assistant.config import (
    DEFAULT_CONFIG_PATH,
    AssistantConfig,
    AssistantConfigError,
    load_config,
    provider_variables,
)
from trading_agent.assistant.memory import Memory
from trading_agent.assistant.prompt import PROMPT_VERSION
from trading_agent.assistant.store import PostgresStore
from trading_agent.assistant.telegram import (
    TelegramClient,
    TokenRejected,
    is_valid_token,
)
from trading_agent.llm.settings import (
    ModelSettings,
    ModelSettingsError,
    require_https_base_url,
)
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.assistant")

DATABASE_VARIABLE = "ASSISTANT_DATABASE_URL"
TOKEN_VARIABLE = "ASSISTANT_TELEGRAM_BOT_TOKEN"
CHAT_VARIABLE = "ASSISTANT_TELEGRAM_CHAT_ID"
PLAN_SCHEMA_NAME = "assistant_plan"
ANSWER_SCHEMA_NAME = "assistant_answer"
HTTP_MARGIN_SECONDS = 10  # the HTTP timeout is the long poll plus this (A2: 50 s and 60 s)

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_DATABASE = 3
EXIT_CRASHED = 4


def build_model(
    provider: str,
    key: str,
    model: ModelSettings,
    *,
    base_url: str | None = None,
    schema_name: str,
):
    if provider == "anthropic":
        from trading_agent.llm.anthropic_client import AnthropicClient

        return AnthropicClient.from_key(key, model)
    from trading_agent.llm.qwen import QwenClient

    return QwenClient(
        key,
        model=model.name,
        max_output_tokens=model.max_output_tokens,
        timeout=model.timeout_seconds,
        base_url=base_url,
        schema_name=schema_name,
    )


def build_store(url: str, config: AssistantConfig) -> PostgresStore:
    return PostgresStore(
        None,
        url,
        ZoneInfo(config.owner_time_zone),
        config.database.statement_timeout_seconds,
        config.limits.max_rows_per_query,
        config.limits.text_field_chars,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    telegram_factory: Callable = TelegramClient,
    model_factory: Callable = build_model,
    store_factory: Callable = build_store,
    config_path: Path = DEFAULT_CONFIG_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> int:
    try:
        return _main(
            argv, telegram_factory, model_factory, store_factory, config_path, clock, sleep, out
        )
    except Exception as exc:  # a crash: never Python's default exit 1 (research A18)
        log.critical("assistant: exiting: crashed: %s", type(exc).__name__)
        return EXIT_CRASHED


def _main(
    argv, telegram_factory, model_factory, store_factory, config_path, clock, sleep, out
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--find-chat-id"]:
        return _find_chat_id(telegram_factory, out)
    if args not in ([], ["--check"]):
        log.critical("assistant: exiting: unknown arguments")
        return EXIT_REFUSED
    try:
        cfg = load_config(config_path)
        settings = _settings(cfg)
    except (ConfigError, AssistantConfigError, ModelSettingsError) as exc:
        log.critical("assistant: exiting: %s", exc)
        return EXIT_REFUSED

    zone = ZoneInfo(cfg.owner_time_zone)
    stop = threading.Event()
    telegram = telegram_factory(
        settings["token"], timeout=cfg.polling.long_poll_seconds + HTTP_MARGIN_SECONDS
    )
    plan_model, answer_model = _models(cfg, settings, model_factory)
    store = store_factory(settings["database_url"], cfg)
    if args:
        return check.run_checks(
            telegram, store, plan_model, answer_model, zone_name=cfg.owner_time_zone,
            config_dir=config_path.parent, now=clock(), out=out,
        )
    memory = Memory(cfg.memory.exchanges, cfg.memory.idle_minutes)

    def respond(update: dict):
        return service.respond(
            update,
            now=clock().astimezone(zone),
            config=cfg,
            store=store,
            plan_model=plan_model,
            answer_model=answer_model,
            memory=memory,
            config_dir=config_path.parent,
        )

    previous = signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
    try:
        log.info(
            "assistant: start prompt_version=%s provider=%s model=%s",
            PROMPT_VERSION, cfg.model.provider, cfg.model.name,
        )
        loop.run(
            telegram,
            chat_id=settings["chat_id"],
            long_poll_seconds=cfg.polling.long_poll_seconds,
            max_failures=cfg.database.max_consecutive_failures,
            respond=respond,
            sleep=sleep,
            stop=stop,
        )
    except TokenRejected:
        log.critical("assistant: exiting: Telegram rejected %s", TOKEN_VARIABLE)
        return EXIT_REFUSED
    except loop.DatabaseLost:
        log.critical("assistant: exiting: the database failed repeatedly")
        return EXIT_DATABASE
    finally:
        signal.signal(signal.SIGTERM, previous)
    log.info("assistant: stopping (SIGTERM)")
    return EXIT_OK


def _find_chat_id(telegram_factory, out) -> int:
    """Setup mode: only the token is needed, and it is never printed."""
    try:
        token = require_env(TOKEN_VARIABLE)
        if not is_valid_token(token):
            raise ConfigError(f"{TOKEN_VARIABLE} is malformed")
    except ConfigError as exc:
        log.critical("assistant: exiting: %s", exc)
        return EXIT_REFUSED
    telegram = telegram_factory(token, timeout=check.POLL_SECONDS + HTTP_MARGIN_SECONDS)
    return check.find_chat_id(telegram, out=out)


def _settings(cfg: AssistantConfig) -> dict:
    """The environment, validated. Names only in any error; a value is never echoed."""
    key_variable, *endpoint = provider_variables(cfg.model.provider)
    database_url = require_env(DATABASE_VARIABLE)
    token = require_env(TOKEN_VARIABLE)
    chat = require_env(CHAT_VARIABLE)
    model_key = require_env(key_variable)
    base_url = require_https_base_url(require_env(endpoint[0]), endpoint[0]) if endpoint else None
    if not is_valid_token(token):
        raise ConfigError(f"{TOKEN_VARIABLE} is malformed")
    if re.fullmatch(r"[0-9]+", chat) is None:
        raise ConfigError(f"{CHAT_VARIABLE} must be an integer")
    return {
        "database_url": database_url,
        "token": token,
        "chat_id": int(chat),
        "model_key": model_key,
        "base_url": base_url,
    }


def _models(cfg: AssistantConfig, settings: dict, factory: Callable):
    def build(schema_name: str):
        return factory(
            cfg.model.provider,
            settings["model_key"],
            cfg.model,
            base_url=settings["base_url"],
            schema_name=schema_name,
        )

    if cfg.model.provider == "anthropic":
        client = build("assistant")  # one client; the schema comes with each call
        return client, client
    return build(PLAN_SCHEMA_NAME), build(ANSWER_SCHEMA_NAME)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
