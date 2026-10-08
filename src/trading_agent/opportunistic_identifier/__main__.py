"""`python -m trading_agent.opportunistic_identifier` (contracts/oi-interface.md; research O12).

One run, then exit; the orchestrator owns the schedule and the timeout. Reads only
OPPORTUNISTIC_IDENTIFIER_* variables (ADR 0015, FR-020), and only the configured provider's
model key. Variables are reported by name, never by value.

Exit codes: 0 ran (reports or a quiet no_action); 1 wrote a failure no_action; 2 refused to
start; 3 database unreachable or a read or write failed; 4 crashed (never Python's default 1,
so a crash isn't mistaken for a recorded failure); 5 the close passed during the run, so
no row could be written (logged).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from trading_agent.llm.settings import ModelSettings, ModelSettingsError, require_https_base_url
from trading_agent.opportunistic_identifier.check import run_check, valid_symbols
from trading_agent.opportunistic_identifier.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_RISK_PATH,
    OIConfigError,
    load_config,
)
from trading_agent.opportunistic_identifier.dry_run import ReadOnlyStore
from trading_agent.opportunistic_identifier.dry_run import lines as dry_run_lines
from trading_agent.opportunistic_identifier.finnhub import OIFinnhub
from trading_agent.opportunistic_identifier.outcome import WINDOW_CLOSED
from trading_agent.opportunistic_identifier.service import OIRun, PgOIStore
from trading_agent.risk import calendar
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.opportunistic_identifier")

DATABASE_VARIABLE = "OPPORTUNISTIC_IDENTIFIER_DATABASE_URL"
FINNHUB_KEY_VARIABLE = "OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY"
# Qwen's endpoint depends on the key's type; kept out of the source, and https only, since the
# key is sent to it.
QWEN_BASE_URL_VARIABLE = "OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL"

CONNECT_TIMEOUT_SECONDS = 10  # within the run budget's margin (research O10)

EXIT_OK = 0
EXIT_FAILURE_RECORDED = 1
EXIT_REFUSED = 2
EXIT_DATABASE = 3
EXIT_CRASHED = 4
EXIT_WINDOW_CLOSED = 5


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
        schema_name="opportunistic_identifier_answer",
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    market_factory: Callable = OIFinnhub,
    model_factory: Callable = build_model,
    connect: Callable = psycopg.connect,
    config_path: Path = DEFAULT_CONFIG_PATH,
    risk_path: Path = DEFAULT_RISK_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    out: Callable[[str], None] = print,
) -> int:
    try:
        return _main(
            argv,
            market_factory,
            model_factory,
            connect,
            config_path,
            risk_path,
            (clock, sleep, monotonic),
            out,
        )
    except Exception as exc:  # a crash: never Python's default exit 1 (research O12)
        log.critical("opportunistic_identifier: crashed: %s", type(exc).__name__)
        return EXIT_CRASHED


def _main(argv, market_factory, model_factory, connect, config_path, risk_path, clocks, out):
    clock, sleep, monotonic = clocks
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = args == ["--dry-run"]
    checking = bool(args) and args[0] == "--check"
    if args and not dry_run and not (checking and valid_symbols(args[1:])):
        log.critical("opportunistic_identifier: unknown or invalid arguments")
        return EXIT_REFUSED

    try:
        cfg, market_key, model_key, database_url, base_url = _settings(
            config_path, risk_path, checking=checking, dry_run=dry_run
        )
    except (ConfigError, OIConfigError) as exc:
        log.critical("opportunistic_identifier: refusing to start: %s", exc)
        return EXIT_REFUSED

    if checking:
        return run_check(
            args[1:],
            market_factory(market_key),
            cfg,
            clock=clock,
            sleep=sleep,
            monotonic=monotonic,
            out=out,
        )

    conn = None
    if database_url is not None:
        try:
            conn = connect(
                database_url,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except psycopg.OperationalError as exc:
            log.critical("opportunistic_identifier: database unreachable: %s", type(exc).__name__)
            return EXIT_DATABASE

    try:
        if dry_run:
            if conn is None:
                out(json.dumps({"note": "no database: open reports not read"}))
            store = ReadOnlyStore(PgOIStore(conn) if conn is not None else None)
        else:
            store = PgOIStore(conn)
        run = OIRun(
            market_factory(market_key),
            model_factory(cfg.model.provider, model_key, cfg.model, base_url=base_url),
            store,
            cfg,
            clock=clock,
            sleep=sleep,
            monotonic=monotonic,
            dry_run=dry_run,
        )
        try:
            outcome = run.run()
        except psycopg.Error as exc:
            log.critical("opportunistic_identifier: database error: %s", type(exc).__name__)
            return EXIT_DATABASE
    finally:
        if conn is not None:
            conn.close()
    if dry_run:
        today = calendar.trading_day(clock())
        for line in dry_run_lines(outcome, closed_day=not calendar.is_session(today)):
            out(line)
    if outcome.failure == WINDOW_CLOSED:
        return EXIT_WINDOW_CLOSED
    return EXIT_FAILURE_RECORDED if outcome.failure is not None else EXIT_OK


def _settings(config_path, risk_path, *, checking: bool, dry_run: bool):
    """The config and the variables this mode needs: `--check` needs no model and no database,
    and a dry run's database is optional. Raises ConfigError naming a variable, never a value."""
    cfg = load_config(config_path, risk_path)
    market_key = require_env(FINNHUB_KEY_VARIABLE)
    if checking:
        return cfg, market_key, None, None, None
    model_key = require_env(cfg.provider_key_variable)
    database_url = _optional(DATABASE_VARIABLE) if dry_run else require_env(DATABASE_VARIABLE)
    base_url = _qwen_base_url() if cfg.model.provider == "qwen" else None
    return cfg, market_key, model_key, database_url, base_url


def _optional(name: str) -> str | None:
    try:
        return require_env(name)
    except ConfigError:
        return None


def _qwen_base_url() -> str:
    value = require_env(QWEN_BASE_URL_VARIABLE)
    try:
        return require_https_base_url(value, QWEN_BASE_URL_VARIABLE)
    except ModelSettingsError as exc:
        # Named, never echoed: the value is configuration, but stays out of logs.
        raise ConfigError(str(exc)) from exc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
