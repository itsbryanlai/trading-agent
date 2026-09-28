"""The Risk Gate's trigger runner loop (ADR 0013): `python -m trading_agent.risk`.

Holds only RISK_GATE_DATABASE_URL, never the broker keys. Exits non-zero on a
lost database connection so the platform restarts it.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from trading_agent.risk.runner import evaluate_pending_triggers
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.risk")

PASS_SECONDS = 60

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_DATABASE_LOST = 3


def main(
    *,
    connect: Callable = psycopg.connect,
    evaluate: Callable = evaluate_pending_triggers,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_passes: int | None = None,
) -> int:
    try:
        url = require_env("RISK_GATE_DATABASE_URL")
    except ConfigError as exc:
        log.critical("risk gate: refusing to start: %s", exc)
        return EXIT_REFUSED
    try:
        conn = connect(url, autocommit=True, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        log.critical("risk gate: database unreachable: %s", exc)
        return EXIT_DATABASE_LOST
    try:
        passes = 0
        while max_passes is None or passes < max_passes:
            if conn.closed:
                raise psycopg.OperationalError("connection closed")
            evaluate(conn, clock())
            passes += 1
            if max_passes is None or passes < max_passes:
                sleep(PASS_SECONDS)
    except psycopg.OperationalError as exc:
        log.critical("risk gate: database connection lost, exiting for a restart: %s", exc)
        return EXIT_DATABASE_LOST
    finally:
        conn.close()
    return EXIT_OK


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
