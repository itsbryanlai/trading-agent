"""`python -m trading_agent.dashboard`: serve, or `--hash-password`.

Contract: specs/014-dashboard/contracts/dashboard-interface.md.

Exit codes: 0 served until SIGTERM or a hash printed; 2 refused (a missing variable, a bad
config file, an unknown flag, a short password or two entries that differ). `--check` arrives
with its own task. The process runs one uvicorn worker: sessions and sign-in delays live in
memory, so a second worker would split them.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import uvicorn

from trading_agent.dashboard import auth, deployed
from trading_agent.dashboard.app import create_app
from trading_agent.dashboard.config import (
    DEFAULT_CONFIG_PATH,
    DashboardConfig,
    DashboardConfigError,
    load_config,
)
from trading_agent.dashboard.store import PostgresStore
from trading_agent.dashboard.throttle import SignInGate
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.dashboard")

MIN_PASSWORD_CHARS = 16
REQUIRED = (
    "DASHBOARD_DATABASE_URL",
    "DASHBOARD_PASSWORD_HASH",
    "DASHBOARD_SESSION_SECRET",
    "PORT",
)


def control_login_configured(environ: Mapping[str, str]) -> str | None:
    """The name of the control login's variable if it is missing, else None.

    A presence check only: nothing here reads the value. T019 moves this into control.py,
    the one module allowed to name that variable.
    """
    name = "DASHBOARD_CONTROL_DATABASE_URL"
    return None if environ.get(name) else name


class _NoControl:
    """The control writer until T019: every write is refused, so nothing can change state."""

    def set_paused(self, expected: bool, target: bool) -> str:
        raise RuntimeError("the control writer is not built yet")


def _fail(message: str) -> int:
    print(f"dashboard: {message}", file=sys.stderr)
    return 2


def hash_password() -> int:
    first = getpass.getpass("Password: ")
    if len(first) < MIN_PASSWORD_CHARS:
        return _fail(f"the password must be at least {MIN_PASSWORD_CHARS} characters")
    if getpass.getpass("Again: ") != first:
        return _fail("the two entries differ")
    print(auth.hash_password(first))
    return 0


def _config_version() -> str:
    return hashlib.sha256(DEFAULT_CONFIG_PATH.read_bytes()).hexdigest()[:8]


def _build(config: DashboardConfig, environ: Mapping[str, str]):
    return create_app(
        store=PostgresStore(
            environ["DASHBOARD_DATABASE_URL"], config.database.statement_timeout_seconds
        ),
        control=_NoControl(),
        sessions=auth.Sessions(config.max_sessions, timedelta(hours=config.session_hours)),
        gate=SignInGate(
            config.sign_in_delay.first_seconds,
            config.sign_in_delay.cap_seconds,
            config.sign_in_delay.tracked_addresses,
        ),
        config=config,
        secret=environ["DASHBOARD_SESSION_SECRET"],
        password_hash=environ["DASHBOARD_PASSWORD_HASH"],
        config_dir=deployed.DEFAULT_CONFIG_DIR,
        clock=lambda: datetime.now(UTC),
        market_open=calendar.market_open,
    )


def serve(environ: Mapping[str, str]) -> int:
    missing = [name for name in REQUIRED if not environ.get(name)]
    control_missing = control_login_configured(environ)
    if control_missing:
        missing.append(control_missing)
    if missing:
        return _fail(f"required variable {missing[0]} is not set")
    try:
        port = int(environ["PORT"])
    except ValueError:
        return _fail("PORT is not a number")
    try:
        config = load_config()
    except DashboardConfigError as exc:
        return _fail(str(exc))
    app = _build(config, environ)
    log.info("dashboard started port=%s config_version=%s", port, _config_version())
    try:
        uvicorn.run(
            app, host="0.0.0.0", port=port, workers=1, access_log=False, proxy_headers=False
        )
    finally:
        log.info("dashboard stopped")
    return 0


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading_agent.dashboard", exit_on_error=False)
    parser.add_argument("--hash-password", action="store_true")
    try:
        args = parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return 2
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    if args.hash_password:
        return hash_password()
    return serve(os.environ if environ is None else environ)


if __name__ == "__main__":
    sys.exit(main())
