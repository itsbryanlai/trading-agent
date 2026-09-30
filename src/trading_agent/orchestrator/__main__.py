"""The orchestrator's own loop (ADR 0013, 0015): `python -m trading_agent.orchestrator`.

Its only credential is ORCHESTRATOR_DATABASE_URL (a ta_orchestrator login). The
agents' variables are set on the same service; the orchestrator passes each agent
only its listed ones and never reads them otherwise (FR-008a). Startup (research
O12): environment and config, database, single-instance lock, reap orphans, then
a tick every 30 seconds. On SIGTERM, SIGINT, the loop ending or a lost database,
every running agent is stopped before exiting (FR-005a). Exits 2 on a refusal to
start and 3 on a lost database; an agent failing never ends the orchestrator.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from trading_agent.orchestrator.config import (
    DEFAULT_CONFIG_PATH,
    ScheduleConfigError,
    load_config,
)
from trading_agent.orchestrator.launcher import SubprocessLauncher
from trading_agent.orchestrator.service import NotAutocommit, Orchestrator, PgRunStore
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.orchestrator")

DATABASE_VARIABLE = "ORCHESTRATOR_DATABASE_URL"
TICK_SECONDS = 30
# "orch": distinct from Execution's single-instance 0x65786531 and per-approval
# 0x65786563, the gate's 0x7269736B and the reference-data job's 0x72656631.
SINGLE_INSTANCE_LOCK = 0x6F726368

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_DATABASE_LOST = 3


class AnotherOrchestratorRunning(Exception):
    """Only one orchestrator may run at a time (FR-021)."""


class Stopping(Exception):
    """Raised from the SIGTERM/SIGINT handler to end the loop cleanly."""


def _raise_stopping(signum, frame):
    raise Stopping(signal.Signals(signum).name)


def main(
    *,
    connect: Callable = psycopg.connect,
    launcher_factory: Callable = SubprocessLauncher,
    store_factory: Callable = PgRunStore,
    config_path: Path = DEFAULT_CONFIG_PATH,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_ticks: int | None = None,
    lock_wait: timedelta = timedelta(minutes=5),
    lock_retry: timedelta = timedelta(seconds=15),
    install_signal_handlers: bool = True,
) -> int:
    try:
        database_url = require_env(DATABASE_VARIABLE)
        config = load_config(config_path)
    except (ConfigError, ScheduleConfigError) as exc:
        log.critical("orchestrator: refusing to start: %s", exc)
        return EXIT_REFUSED

    try:
        conn = connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )
    except psycopg.OperationalError as exc:
        log.critical("orchestrator: database unreachable: %s", type(exc).__name__)
        return EXIT_DATABASE_LOST

    orchestrator = None
    code = EXIT_OK
    try:
        for setting in (
            "tcp_keepalives_idle = 30",
            "tcp_keepalives_interval = 10",
            "tcp_keepalives_count = 3",
        ):
            conn.execute(f"SET {setting}")
        _take_lock(conn, lock_wait, lock_retry, sleep)
        # Looked up at the moment an agent starts, never before: the orchestrator
        # never holds or reads an agent's value otherwise (FR-008a).
        orchestrator = Orchestrator(
            config, store_factory(conn), launcher_factory(), lambda name: os.environ.get(name)
        )
        orchestrator.startup(clock())
        if install_signal_handlers:
            signal.signal(signal.SIGTERM, _raise_stopping)
            signal.signal(signal.SIGINT, _raise_stopping)
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            if conn.closed:
                raise psycopg.OperationalError("connection closed")
            began = monotonic()
            orchestrator.tick(clock())
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                # Ticks start every 30 s; a long tick is followed at once by the next.
                sleep(max(0.0, TICK_SECONDS - (monotonic() - began)))
    except Stopping as exc:
        log.info("orchestrator: %s received, stopping", exc)
    except (NotAutocommit, AnotherOrchestratorRunning) as exc:
        log.critical("orchestrator: refusing to start: %s", exc)
        code = EXIT_REFUSED
    except psycopg.OperationalError as exc:
        log.critical(
            "orchestrator: database connection lost, exiting for a restart: %s",
            type(exc).__name__,
        )
        code = EXIT_DATABASE_LOST
    finally:
        if orchestrator is not None:
            orchestrator.shutdown(clock())
        conn.close()
    return code


def _take_lock(conn, lock_wait: timedelta, lock_retry: timedelta, sleep) -> None:
    attempts = max(1, int(lock_wait / lock_retry) + 1)
    for attempt in range(1, attempts + 1):
        row = conn.execute("SELECT pg_try_advisory_lock(%s) AS mine", (SINGLE_INSTANCE_LOCK,))
        if row.fetchone()["mine"]:
            return
        if attempt == attempts:
            raise AnotherOrchestratorRunning(
                f"another orchestrator still holds the lock after {lock_wait}"
            )
        log.warning(
            "orchestrator: single-instance lock held elsewhere (attempt %d of %d); waiting",
            attempt,
            attempts,
        )
        sleep(lock_retry.total_seconds())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
