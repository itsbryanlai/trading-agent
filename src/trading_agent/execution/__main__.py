"""Execution's own loop (ADR 0013): `python -m trading_agent.execution`.

Holds only the paper broker keys and the ta_execution login; never the Risk
Gate's (research E13, E15). Refuses to start unless provably on the paper
account; exits non-zero on a lost database connection so the platform restarts
it rather than logging failures forever.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from trading_agent.execution.alpaca import AlpacaBroker
from trading_agent.execution.broker import NotPaperTrading
from trading_agent.execution.service import AnotherExecutionRunning, Executor, NotAutocommit
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.execution")

TICK_SECONDS = 60

EXIT_OK = 0
EXIT_REFUSED = 2  # not the paper account, or misconfigured
EXIT_DATABASE_LOST = 3


def main(
    *,
    broker_factory: Callable = AlpacaBroker,
    connect: Callable = psycopg.connect,
    executor_factory: Callable = Executor,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_ticks: int | None = None,
) -> int:
    try:
        broker = broker_factory(
            require_env("ALPACA_API_KEY_ID"),
            require_env("ALPACA_API_SECRET_KEY"),
            os.environ.get("ALPACA_BASE_URL") or None,
        )
        database_url = require_env("EXECUTION_DATABASE_URL")
    except (ConfigError, NotPaperTrading) as exc:
        log.critical("execution: refusing to start: %s", exc)
        return EXIT_REFUSED

    try:
        # autocommit: each unit of work is a real transaction (research E5);
        # storage.db.connect would commit only when the connection closes.
        conn = connect(database_url, autocommit=True, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        log.critical("execution: database unreachable: %s", exc)
        return EXIT_DATABASE_LOST

    try:
        executor = executor_factory(broker, conn)
        executor.startup()
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            if conn.closed:
                raise psycopg.OperationalError("connection closed")
            report = executor.tick(clock())
            log.info("execution: tick %s", report)
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                sleep(TICK_SECONDS)
    except (NotPaperTrading, NotAutocommit, AnotherExecutionRunning) as exc:
        log.critical("execution: refusing to start: %s", exc)
        return EXIT_REFUSED
    except psycopg.OperationalError as exc:
        log.critical("execution: database connection lost, exiting for a restart: %s", exc)
        return EXIT_DATABASE_LOST
    finally:
        conn.close()
    return EXIT_OK


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
