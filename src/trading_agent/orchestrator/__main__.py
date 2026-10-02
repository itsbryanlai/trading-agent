"""The orchestrator's own loop (ADR 0013, 0015): `python -m trading_agent.orchestrator`.

Its only credential is ORCHESTRATOR_DATABASE_URL (a ta_orchestrator login). The
agents' variables are set on the same service; the orchestrator passes each agent
only its listed ones and never reads them otherwise (FR-008a). Startup (research
O12): environment and config, database, single-instance lock, reap orphans, then
a tick every 30 seconds. On SIGTERM, SIGINT, the loop ending or a database error,
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


class StopFlag:
    """Set by SIGTERM or SIGINT and checked between ticks.

    The handler only records the request. Raising from a handler could interrupt
    the code anywhere, including between starting an agent's process and tracking
    it, which would leave that agent running with no record of its process group
    (adversarial review M3). A second signal just sets the flag again, so it can't
    cut the shutdown short.
    """

    def __init__(self) -> None:
        self.requested: str | None = None

    def handler(self, signum, frame) -> None:
        self.requested = signal.Signals(signum).name


def main(  # noqa: PLR0915 - baseline; split when next touched
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
    stop: StopFlag | None = None,
    install_signal_handlers: bool = True,
) -> int:
    stop = stop or StopFlag()
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
            signal.signal(signal.SIGTERM, stop.handler)
            signal.signal(signal.SIGINT, stop.handler)
        ticks = 0
        while stop.requested is None and (max_ticks is None or ticks < max_ticks):
            if conn.closed:
                raise psycopg.OperationalError("connection closed")
            began = monotonic()
            orchestrator.tick(clock())
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                # Ticks start every 30 s; a long tick is followed at once by the next.
                # Slept a second at a time, so a stop request is noticed promptly.
                remaining = max(0.0, TICK_SECONDS - (monotonic() - began))
                slept = 0.0
                while slept < remaining and stop.requested is None:
                    step = min(1.0, remaining - slept)
                    sleep(step)
                    slept += step
        if stop.requested is not None:
            log.info("orchestrator: %s received, stopping", stop.requested)
    except (NotAutocommit, AnotherOrchestratorRunning) as exc:
        log.critical("orchestrator: refusing to start: %s", exc)
        code = EXIT_REFUSED
    except psycopg.OperationalError as exc:
        log.critical(
            "orchestrator: database connection lost, exiting for a restart: %s",
            type(exc).__name__,
        )
        code = EXIT_DATABASE_LOST
    except psycopg.Error as exc:
        # Not a lost connection, but nothing can be scheduled safely either: say
        # so plainly and let the platform restart us (adversarial review L5).
        log.critical("orchestrator: database error, exiting for a restart: %s", type(exc).__name__)
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
