"""The reference-data job's own loop (ADR 0013): `python -m trading_agent.reference`.

Holds only its read-only market-data key and the ta_reference_data login
(FR-020). Startup (research D13): environment, config, one read-only call to
check the key, database, single-instance lock. Exits 2 on a refusal to start and
3 on a lost database; provider errors after startup never end the process.

`python -m trading_agent.reference --check SYMBOL...` fetches and normalizes the
given symbols, prints them and writes nothing (D12). It needs only the key; the
owner runs it once before deploying to confirm the provider's units and labels.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from trading_agent.reference import normalize as n
from trading_agent.reference.config import DEFAULT_CONFIG_PATH, ReferenceConfigError, load_config
from trading_agent.reference.finnhub import FinnhubProvider
from trading_agent.reference.provider import (
    KeyRejected,
    NotPermitted,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from trading_agent.reference.service import NotAutocommit, PgReferenceStore, ReferenceJob
from trading_agent.reference.symbols import is_plausible_ticker
from trading_agent.risk import calendar
from trading_agent.storage.db import ConfigError, require_env

log = logging.getLogger("trading_agent.reference")

KEY_VARIABLE = "REFERENCE_DATA_FINNHUB_API_KEY"
DATABASE_VARIABLE = "REFERENCE_DATA_DATABASE_URL"
TICK_SECONDS = 60
# "ref1": distinct from Execution's single-instance 0x65786531 and its
# per-approval 0x65786563, and from the gate's 0x7269736B.
SINGLE_INSTANCE_LOCK = 0x72656631

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_DATABASE_LOST = 3


class AnotherJobRunning(Exception):
    """Only one reference-data job may run at a time (FR-017)."""


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: Callable = FinnhubProvider,
    connect: Callable = psycopg.connect,
    job_factory: Callable = ReferenceJob,
    store_factory: Callable = PgReferenceStore,
    config_path: Path = DEFAULT_CONFIG_PATH,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_ticks: int | None = None,
    lock_wait: timedelta = timedelta(minutes=5),
    lock_retry: timedelta = timedelta(seconds=15),
    out: Callable[[str], None] = print,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--check"]:
        return _check(
            args[1:], provider_factory=provider_factory, sleep=sleep, clock=clock, out=out
        )
    if args:
        log.critical("reference: unknown arguments %s", args)
        return EXIT_REFUSED

    try:
        key = require_env(KEY_VARIABLE)
        database_url = require_env(DATABASE_VARIABLE)
        config = load_config(config_path)
    except (ConfigError, ReferenceConfigError) as exc:
        log.critical("reference: refusing to start: %s", exc)
        return EXIT_REFUSED

    provider = provider_factory(key)
    started_at = clock()
    symbol_list = None
    try:
        # The key check (FR-019a), and today's symbol list while we're at it.
        symbol_list = (calendar.trading_day(started_at), provider.list_us_symbols())
    except KeyRejected as exc:
        log.critical("reference: refusing to start: market-data key rejected: %s", exc)
        return EXIT_REFUSED
    except ProviderError as exc:
        # An outage or rate limit isn't a misconfiguration: exiting would only make
        # the platform restart us into the same call (review M4). The first tick
        # fetches the list, and a bad key is then caught there (FR-019a).
        log.warning("reference: market-data key check deferred: %s", exc)

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
        log.critical("reference: database unreachable: %s", type(exc).__name__)
        return EXIT_DATABASE_LOST

    try:
        for setting in (
            "tcp_keepalives_idle = 30",
            "tcp_keepalives_interval = 10",
            "tcp_keepalives_count = 3",
        ):
            conn.execute(f"SET {setting}")
        _take_lock(conn, lock_wait, lock_retry, sleep)
        job = job_factory(
            provider,
            store_factory(conn),
            config,
            sleep=sleep,
            monotonic=monotonic,
            symbol_list=symbol_list,
        )
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            if conn.closed:
                raise psycopg.OperationalError("connection closed")
            began = monotonic()
            job.tick(clock())
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                # Ticks start every 60 s; a long tick is followed at once by the next.
                sleep(max(0.0, TICK_SECONDS - (monotonic() - began)))
    except (NotAutocommit, AnotherJobRunning) as exc:
        log.critical("reference: refusing to start: %s", exc)
        return EXIT_REFUSED
    except psycopg.OperationalError as exc:
        log.critical(
            "reference: database connection lost, exiting for a restart: %s", type(exc).__name__
        )
        return EXIT_DATABASE_LOST
    finally:
        conn.close()
    return EXIT_OK


def _take_lock(conn, lock_wait: timedelta, lock_retry: timedelta, sleep) -> None:
    """Held for the connection's lifetime. A previous process's lock can outlive it
    briefly (a half-open connection), so wait before giving up, as Execution does."""
    attempts = max(1, int(lock_wait / lock_retry) + 1)
    for attempt in range(1, attempts + 1):
        row = conn.execute("SELECT pg_try_advisory_lock(%s) AS mine", (SINGLE_INSTANCE_LOCK,))
        if row.fetchone()["mine"]:
            return
        if attempt == attempts:
            raise AnotherJobRunning(
                f"another reference-data job still holds the lock after {lock_wait}"
            )
        log.warning(
            "reference: single-instance lock held elsewhere (attempt %d of %d); waiting",
            attempt,
            attempts,
        )
        sleep(lock_retry.total_seconds())


def _check(symbols, *, provider_factory, sleep, clock, out) -> int:
    """Owner-run, read-only: print what the job would record (D12). No database."""
    try:
        provider = provider_factory(require_env(KEY_VARIABLE))
        listings = provider.list_us_symbols()
    except ConfigError as exc:
        log.critical("reference: %s", exc)
        return EXIT_REFUSED
    except ProviderError as exc:
        out(f"market-data key check failed: {exc}")
        return EXIT_REFUSED

    for symbol in symbols:
        if not is_plausible_ticker(symbol):
            out(f"{symbol} failed: {n.INVALID_SYMBOL}")
            continue
        listing = listings.get(symbol)
        try:
            profile = provider.get_profile(symbol)
            sleep(1)
            quote = provider.get_quote(symbol)
            sleep(1)
            metrics = provider.get_metrics(symbol)
            sleep(1)
        except KeyRejected as exc:
            out(f"market-data key rejected: {exc}")
            return EXIT_REFUSED
        except RateLimited:
            out(f"{symbol} failed: {n.RATE_LIMITED}")
            continue
        except NotPermitted:
            out(f"{symbol} failed: {n.NOT_PERMITTED}")
            continue
        except ProviderUnavailable:
            out(f"{symbol} failed: {n.PROVIDER_UNAVAILABLE}")
            continue
        result = n.normalize(symbol, listing, profile, quote, metrics, clock())
        # What the mappings and the stale-quote rule received (quickstart step 4).
        when = quote.timestamp.isoformat() if quote.timestamp else None
        raw = (f"(provider type={listing.type!r} mic={listing.mic!r} " if listing else "(") + (
            f"currency={profile.currency!r} c={quote.current} pc={quote.previous_close} t={when})"
        )
        if isinstance(result, n.Failure):
            out(f"{symbol} failed: {result.reason} {raw}")
        else:
            out(
                f"{symbol} {result.security_type} {result.exchange_mic} "
                f"market_cap_usd={result.market_cap_usd:,} "
                f"avg_daily_dollar_volume_usd={result.avg_daily_dollar_volume_usd:,} "
                f"share_price_usd={result.share_price_usd} {raw}"
            )
    return EXIT_OK


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
