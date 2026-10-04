"""`python -m trading_agent.portfolio_manager`: one run, then exit
(specs/008-portfolio-manager contracts/pm-interface.md; research P2, P9).

Reads its environment (names only are ever logged), loads config, connects as
`ta_portfolio_manager`, builds the quote source (the reference job's Finnhub adapter) and
the model client, runs once, and maps the outcome to an exit code.

Seams for later stories: the dry run and its arguments (User Story 5), and the full exit
mapping with the crash handler (User Story 4, tasks T032). For now: 0 for a completed run,
1 for a failure the service reported, 2 when it refuses to start, 3 when the database is
unreachable or a read or the write fails.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from trading_agent.llm.settings import (
    ModelSettings,
    ModelSettingsError,
    require_https_base_url,
)
from trading_agent.portfolio_manager import service
from trading_agent.portfolio_manager.config import (
    DEFAULT_CONFIG_PATH,
    PortfolioManagerConfigError,
    load_config,
)
from trading_agent.portfolio_manager.store import PostgresStore, StoreError
from trading_agent.reference.finnhub import FinnhubProvider
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.portfolio_manager")

DATABASE_VARIABLE = "PORTFOLIO_MANAGER_DATABASE_URL"
QUOTE_KEY_VARIABLE = "PORTFOLIO_MANAGER_FINNHUB_API_KEY"
SCHEMA_NAME = "pm_answer"
CONNECT_TIMEOUT_SECONDS = 10

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_REFUSED = 2
EXIT_DATABASE = 3


def build_model(provider: str, key: str, model: ModelSettings, *, base_url: str | None = None):
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
        schema_name=SCHEMA_NAME,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    quotes_factory: Callable = FinnhubProvider,
    model_factory: Callable = build_model,
    connect: Callable = psycopg.connect,
    store_factory: Callable = PostgresStore,
    config_path: Path = DEFAULT_CONFIG_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        log.critical("portfolio_manager: unknown arguments %s", args)
        return EXIT_REFUSED

    try:
        cfg = load_config(config_path)
        key_variable, *endpoint = cfg.provider_variables
        quote_key = require_env(QUOTE_KEY_VARIABLE)
        model_key = require_env(key_variable)
        base_url = _endpoint(endpoint[0]) if endpoint else None
        database_url = require_env(DATABASE_VARIABLE)
    except (ConfigError, PortfolioManagerConfigError) as exc:
        log.critical("portfolio_manager: refusing to start: %s", exc)
        return EXIT_REFUSED

    try:
        conn = connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except psycopg.OperationalError as exc:
        log.critical("portfolio_manager: database unreachable: %s", type(exc).__name__)
        return EXIT_DATABASE

    try:
        outcome = service.run(
            clock=clock,
            config=cfg,
            store=store_factory(conn),
            quotes=quotes_factory(quote_key),
            model=model_factory(cfg.model.provider, model_key, cfg.model, base_url=base_url),
            sleep=sleep,
        )
    except (StoreError, psycopg.Error) as exc:
        log.critical("portfolio_manager: database error: %s", type(exc).__name__)
        return EXIT_DATABASE
    finally:
        conn.close()
    return EXIT_FAILURE if outcome.failure is not None else EXIT_OK


def _endpoint(variable: str) -> str:
    try:
        return require_https_base_url(require_env(variable), variable)
    except ModelSettingsError as exc:
        # Named, never echoed: the value is configuration, but stays out of logs.
        raise ConfigError(str(exc)) from exc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
