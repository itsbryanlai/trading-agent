"""Create each component's database login, run by the owner with the admin credential.

    python -m trading_agent.storage.logins --service-host HOST[:PORT] [--reset NAME ...]

Run on the owner's machine with ADMIN_DATABASE_URL exported in the shell, never by
a service (specs/010-observe-only-deployment/contracts/logins-command.md). Each
login joins exactly one group role and gets a random password, printed once to
stdout as a connection string. Nothing is written to disk. The admin URL and its
password are never printed or logged, and no database error text is shown, only
its type.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from collections.abc import Callable, Sequence
from typing import NamedTuple
from urllib.parse import quote

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from trading_agent.storage.db import ConfigError, connect, require_env

ADMIN_VARIABLE = "ADMIN_DATABASE_URL"
DEFAULT_PORT = 5432
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_DATABASE_LOST = 3
# A failure can land after the transaction began, or after it committed but before the
# reply arrived, so "nothing was created" would not always be true.
RERUN_HINT = "re-run to check; use --reset if a login shows as existing"


class Login(NamedTuple):
    name: str
    group: str


# The contract's nine rows. Each login is a member of exactly one group role.
LOGINS: tuple[Login, ...] = (
    Login("ta_orchestrator_login", "ta_orchestrator"),
    Login("ta_research_login", "ta_research"),
    Login("ta_opportunistic_identifier_login", "ta_opportunistic_identifier"),
    Login("ta_portfolio_manager_login", "ta_portfolio_manager"),
    Login("ta_risk_gate_login", "ta_risk_gate"),
    Login("ta_reference_data_login", "ta_reference_data"),
    Login("ta_execution_login", "ta_execution"),
    Login("ta_owner_read_login", "ta_dashboard"),
    Login("ta_owner_control_login", "ta_dashboard_control"),
)


class LoginsError(Exception):
    """A refusal (exit 2). The message never holds a credential."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise LoginsError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python -m trading_agent.storage.logins", add_help=False, allow_abbrev=False
    )
    parser.add_argument("--service-host", required=True, metavar="HOST[:PORT]")
    parser.add_argument("--reset", action="append", default=[], metavar="NAME")
    return parser


def split_host(value: str) -> tuple[str, int]:
    """`HOST` or `HOST:PORT` (also `[v6]:PORT`); the port defaults to 5432."""
    host, port = value, DEFAULT_PORT
    if value.startswith("["):
        end = value.find("]")
        host, rest = value[: end + 1], value[end + 1 :]
        if end < 0 or (rest and not rest.startswith(":")):
            raise LoginsError("--service-host: must be HOST or HOST:PORT")
        port_text = rest[1:]
    elif value.count(":") == 1:
        host, port_text = value.split(":")
    else:
        port_text = ""
    if port_text:
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise LoginsError("--service-host: the port must be a number from 1 to 65535")
        port = int(port_text)
    if (
        not host
        or any(ch in host for ch in "/@?# \t")
        or (":" in host and not host.startswith("["))
    ):
        raise LoginsError("--service-host: must be HOST or HOST:PORT")
    return host, port


def parse_args(argv: Sequence[str]) -> tuple[str, int, list[str]]:
    args = _parser().parse_args(list(argv))
    host, port = split_host(args.service_host)
    known = {login.name for login in LOGINS}
    for name in args.reset:
        if name not in known:
            raise LoginsError(f"--reset: unknown login {name!r}")
    return host, port, list(dict.fromkeys(args.reset))


def database_name(admin_url: str) -> str:
    """The database the printed strings point at, taken from the admin URL."""
    try:
        parts = conninfo_to_dict(admin_url)
    except psycopg.Error as exc:
        # The parser's message can quote the string; say only that it is bad.
        raise LoginsError(f"{ADMIN_VARIABLE}: not a valid connection string") from exc
    name = parts.get("dbname") or parts.get("user")
    if not name:
        raise LoginsError(f"{ADMIN_VARIABLE}: names no database")
    return name


def connection_string(name: str, password: str, host: str, port: int, dbname: str) -> str:
    user, secret, db = (quote(part, safe="") for part in (name, password, dbname))
    return f"postgresql://{user}:{secret}@{host}:{port}/{db}"


def new_password() -> str:
    return secrets.token_urlsafe(32)


def _present(conn: psycopg.Connection, names: Sequence[str]) -> set[str]:
    rows = conn.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(names),))
    return {row["rolname"] for row in rows.fetchall()}


def _verifier(conn: psycopg.Connection, name: str, password: str) -> str:
    """The SCRAM-SHA-256 verifier the server stores, so the plain password never
    travels in the statement (and so never reaches the server log)."""
    return conn.pgconn.encrypt_password(password.encode(), name.encode(), b"scram-sha-256").decode()


def _existing_logins(conn: psycopg.Connection, names: Sequence[str]) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole,"
        " coalesce(array_agg(g.rolname::text) FILTER (WHERE g.rolname IS NOT NULL),"
        " '{}'::text[]) AS groups"
        " FROM pg_roles r"
        " LEFT JOIN pg_auth_members m ON m.member = r.oid"
        " LEFT JOIN pg_roles g ON g.oid = m.roleid"
        " WHERE r.rolname = ANY(%s)"
        " GROUP BY r.oid, r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole",
        (list(names),),
    )
    return {row["rolname"]: row for row in rows.fetchall()}


def _check_existing(found: dict[str, dict]) -> None:
    """Refuse when an existing login is not exactly what the contract says."""
    group_of = {login.name: login.group for login in LOGINS}
    for name, row in sorted(found.items()):
        flags = (row["rolsuper"], row["rolcreatedb"], row["rolcreaterole"])
        if any(flags) or sorted(row["groups"]) != [group_of[name]]:
            raise LoginsError(
                f"{name} exists but is not a NOSUPERUSER NOCREATEDB NOCREATEROLE member of"
                f" exactly {group_of[name]}: fix or drop it as the administrator, then re-run."
                " Nothing was changed."
            )


def _create(conn: psycopg.Connection, login: Login, password: str) -> None:
    conn.execute(
        sql.SQL(
            "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "PASSWORD {} IN ROLE {}"
        ).format(
            sql.Identifier(login.name),
            sql.Literal(_verifier(conn, login.name, password)),
            sql.Identifier(login.group),
        )
    )


def _repassword(conn: psycopg.Connection, name: str, password: str) -> None:
    conn.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(name), sql.Literal(_verifier(conn, name, password))
        )
    )


def apply(
    admin_url: str,
    host: str,
    port: int,
    resets: Sequence[str],
    connect_fn: Callable[[str], object] = connect,
) -> list[str]:
    """Create the missing logins in one transaction; returns the lines to print.

    The lines are returned, not printed, so a rolled-back run prints no string.
    """
    dbname = database_name(admin_url)
    lines: list[str] = []
    with connect_fn(admin_url) as conn:
        groups = {login.group for login in LOGINS}
        missing = sorted(groups - _present(conn, sorted(groups)))
        if missing:
            raise LoginsError(f"group roles missing ({', '.join(missing)}): run migrate first")
        found = _existing_logins(conn, [login.name for login in LOGINS])
        _check_existing(found)
        existing = set(found)
        absent = [name for name in resets if name not in existing]
        if absent:
            raise LoginsError(f"--reset: {', '.join(absent)} does not exist yet")
        for login in LOGINS:
            if login.name in existing and login.name not in resets:
                lines.append(f"{login.name}  exists, unchanged")
                continue
            password = new_password()
            if login.name in existing:
                _repassword(conn, login.name, password)
            else:
                _create(conn, login, password)
            lines.append(
                f"{login.name}  {connection_string(login.name, password, host, port, dbname)}"
            )
    return lines


def main(
    argv: Sequence[str] | None = None, *, connect_fn: Callable[[str], object] = connect
) -> int:
    try:
        host, port, resets = parse_args(sys.argv[1:] if argv is None else argv)
        admin_url = require_env(ADMIN_VARIABLE)
        lines = apply(admin_url, host, port, resets, connect_fn)
    except (LoginsError, ConfigError) as exc:
        print(f"logins refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except psycopg.OperationalError as exc:
        # Only the type: a driver message can name the host or the user.
        print(f"database unreachable: {type(exc).__name__}; {RERUN_HINT}", file=sys.stderr)
        return EXIT_DATABASE_LOST
    except Exception as exc:
        print(f"logins failed: {type(exc).__name__}; {RERUN_HINT}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
