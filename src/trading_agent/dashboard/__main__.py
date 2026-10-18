"""`python -m trading_agent.dashboard`: serve, `--check`, or `--hash-password`.

Contract: specs/014-dashboard/contracts/dashboard-interface.md.

Exit codes: 0 served until SIGTERM, every check ok, or a hash printed; 1 a check failed;
2 refused (a missing variable, a short secret or malformed hash, a bad config file, an unknown
flag, a short password or two entries that differ). The process runs one uvicorn worker:
sessions and sign-in delays live in memory, so a second worker would split them.
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

from trading_agent.dashboard import auth, check, deployed
from trading_agent.dashboard.app import create_app
from trading_agent.dashboard.config import (
    DEFAULT_CONFIG_PATH,
    DashboardConfig,
    DashboardConfigError,
    load_config,
)
from trading_agent.dashboard.control import ControlWriter, control_login_configured
from trading_agent.dashboard.store import PostgresStore
from trading_agent.dashboard.throttle import SignInGate
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.dashboard")

MIN_PASSWORD_CHARS = 16
CHECK_REQUIRED = (
    "DASHBOARD_DATABASE_URL",
    "DASHBOARD_PASSWORD_HASH",
    "DASHBOARD_SESSION_SECRET",
)
REQUIRED = (*CHECK_REQUIRED, "PORT")
CHECK_TIMEOUT_SECONDS = 10


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
        control=ControlWriter.from_env(environ),
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


def _missing(environ: Mapping[str, str], names: Sequence[str]) -> str | None:
    """The first required variable that is unset or empty."""
    absent = [name for name in names if not environ.get(name)]
    control_missing = control_login_configured(environ)
    if control_missing:
        absent.append(control_missing)
    return absent[0] if absent else None


def _weak_value(environ: Mapping[str, str]) -> str | None:
    """A message naming a variable whose value fails its check, never the value."""
    checks = (
        ("DASHBOARD_PASSWORD_HASH", check.password_hash_code),
        ("DASHBOARD_SESSION_SECRET", check.session_secret_code),
    )
    for name, validate in checks:
        code = validate(environ[name])
        if code:
            return f"{name} is not acceptable ({code})"
    return None


def run_check(environ: Mapping[str, str]) -> int:
    absent = _missing(environ, CHECK_REQUIRED)
    if absent:
        return _fail(f"required variable {absent} is not set")
    results = check.run(
        store=PostgresStore(environ["DASHBOARD_DATABASE_URL"], CHECK_TIMEOUT_SECONDS),
        control=ControlWriter.from_env(environ),
        password_hash=environ["DASHBOARD_PASSWORD_HASH"],
        secret=environ["DASHBOARD_SESSION_SECRET"],
        config_dir=deployed.DEFAULT_CONFIG_DIR,
    )
    for result in results:
        print(result.line())
    return 0 if all(result.ok for result in results) else 1


def serve(environ: Mapping[str, str]) -> int:
    absent = _missing(environ, REQUIRED)
    if absent:
        return _fail(f"required variable {absent} is not set")
    weak = _weak_value(environ)
    if weak:
        return _fail(weak)
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
    parser.add_argument("--check", action="store_true")
    try:
        args = parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return 2
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    if args.hash_password:
        return hash_password()
    if args.check:
        return run_check(os.environ if environ is None else environ)
    return serve(os.environ if environ is None else environ)


if __name__ == "__main__":
    sys.exit(main())
