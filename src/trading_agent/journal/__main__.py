"""`python -m trading_agent.journal` (contracts/journal-interface.md; research J12).

One run, then exit. Railway's cron starts it after the close. Reads only JOURNAL_* variables
(ADR 0015, ADR 0022), reported by name and never by value.

Exit codes: 0 wrote, or nothing to do; 1 a named failure, nothing written; 2 refused to start;
3 the database is unreachable or a read or write failed; 4 crashed (never Python's default 1,
so a crash isn't mistaken for a named failure).
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

from trading_agent.journal import service
from trading_agent.journal.config import (
    DEFAULT_CONFIG_PATH,
    JournalConfig,
    JournalConfigError,
    load_config,
)
from trading_agent.journal.store import PgJournalStore
from trading_agent.reference.finnhub import FinnhubProvider
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.journal")

DATABASE_VARIABLE = "JOURNAL_DATABASE_URL"
FINNHUB_KEY_VARIABLE = "JOURNAL_FINNHUB_API_KEY"
CONNECT_TIMEOUT_SECONDS = 10

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_REFUSED = 2
EXIT_DATABASE = 3
EXIT_CRASHED = 4


def main(
    argv: Sequence[str] | None = None,
    *,
    market_factory: Callable = FinnhubProvider,
    connect: Callable = psycopg.connect,
    store_factory: Callable = PgJournalStore,
    config_path: Path = DEFAULT_CONFIG_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    out: Callable[[str], None] = print,
) -> int:
    try:
        return _main(
            list(sys.argv[1:] if argv is None else argv),
            market_factory,
            connect,
            store_factory,
            config_path,
            (clock, sleep, monotonic),
            out,
        )
    except Exception as exc:  # a crash: never Python's default exit 1
        log.critical("journal: crashed: %s", type(exc).__name__)
        return EXIT_CRASHED


def _main(args, market_factory, connect, store_factory, config_path, clocks, out) -> int:
    dry_run = args == ["--dry-run"]
    if args and not dry_run:
        log.critical("journal: unknown or invalid arguments")
        return EXIT_REFUSED
    try:
        cfg = load_config(config_path)
        key = require_env(FINNHUB_KEY_VARIABLE)
        database_url = require_env(DATABASE_VARIABLE)
    except (ConfigError, JournalConfigError) as exc:
        log.critical("journal: refusing to start: %s", exc)
        return EXIT_REFUSED

    try:
        conn = connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except psycopg.OperationalError as exc:
        log.critical("journal: database unreachable: %s", type(exc).__name__)
        return EXIT_DATABASE
    try:
        return _run(cfg, market_factory(key), store_factory(conn), clocks, dry_run, out)
    except psycopg.Error as exc:
        log.critical("journal: database error: %s", type(exc).__name__)
        return EXIT_DATABASE
    finally:
        conn.close()


def _run(cfg: JournalConfig, market, store, clocks, dry_run: bool, out) -> int:
    clock, sleep, monotonic = clocks
    outcome = service.run(
        store, market, cfg, now=clock(), sleep=sleep, monotonic=monotonic, dry_run=dry_run
    )
    if outcome.status == "dry_run":
        row = outcome.row
        out(
            json.dumps(
                {
                    "trading_day": row.trading_day.isoformat(),
                    "equity_open": str(row.equity_open),
                    "equity_close": str(row.equity_close),
                }
            )
        )
        out(json.dumps({"summary_md": row.summary_md}))
        out(json.dumps({"per_agent_attribution": row.per_agent_attribution}))
    return EXIT_FAILURE if outcome.status == "failed" else EXIT_OK


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
