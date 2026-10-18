"""`--check`: confirm each credential works and is what it should be, writing nothing.

Contract: specs/014-dashboard/contracts/dashboard-interface.md ("`--check` output"); research
D16. One line per item, `<name>: ok` or `<name>: FAILED <code>`. A code names the reason or the
exception type; no value, connection string or database message is ever printed.

The password-hash and session-secret validators here are also used at start-up, so a weak or
mistyped value stops the service instead of being accepted silently.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from trading_agent.dashboard import auth, deployed
from trading_agent.dashboard.config import DashboardConfigError, load_config
from trading_agent.dashboard.control import ControlError
from trading_agent.dashboard.store import StoreError

# The only scrypt cost the dashboard accepts: `auth.parse_hash` takes up to n=2**20, r=32,
# and a mistyped hash like that would make each sign-in allocate gigabytes.
EXPECTED_SCRYPT = (auth.SCRYPT_N, auth.SCRYPT_R, auth.SCRYPT_P)
MIN_SECRET_BYTES = 32
MIN_SECRET_DISTINCT_BYTES = 16  # 32 copies of one character is not a random secret
DIGEST_BYTES = 32
PRIVILEGE_FLAGS = ("rolsuper", "rolcreaterole", "rolcreatedb", "rolbypassrls")
READ_LOGIN, READ_ROLE = "ta_dashboard_login", "ta_dashboard"
CONTROL_LOGIN, CONTROL_ROLE = "ta_dashboard_control_login", "ta_dashboard_control"


@dataclass(frozen=True)
class Result:
    name: str
    code: str | None  # None: ok

    @property
    def ok(self) -> bool:
        return self.code is None

    def line(self) -> str:
        return f"{self.name}: ok" if self.ok else f"{self.name}: FAILED {self.code}"


def password_hash_code(value: str) -> str | None:
    """None when `value` is `scrypt$32768$8$1$<salt>$<32-byte hash>`, else `malformed`."""
    try:
        parsed = auth.parse_hash(value)
    except auth.MalformedHash:
        return "malformed"
    if (parsed.n, parsed.r, parsed.p) != EXPECTED_SCRYPT or len(parsed.digest) != DIGEST_BYTES:
        return "malformed"
    return None


def session_secret_code(value: str) -> str | None:
    raw = value.encode("utf-8")
    long_enough = len(raw) >= MIN_SECRET_BYTES
    return None if long_enough and len(set(raw)) >= MIN_SECRET_DISTINCT_BYTES else "too_short"


def role_code(
    login: Mapping, roles: Sequence[str], expected_login: str, expected_role: str
) -> str | None:
    """The role rules (contracts/queries.md "`--check`", analyze S6), in this order:
    the exact login name, no `rol*` privilege, member of the expected group role, and of no
    other `ta_*` group role."""
    if login["login"] != expected_login:
        return "wrong_login"
    if any(login[flag] for flag in PRIVILEGE_FLAGS):
        return "privileged"
    if expected_role not in roles:
        return f"wrong_role:{expected_role}"
    extra = sorted(set(roles) - {expected_role})
    return f"extra_role:{extra[0]}" if extra else None


def _database(
    reader: Callable[[], tuple[Mapping, Sequence[str]]], login: str, role: str
) -> str | None:
    try:
        info, roles = reader()
    except (StoreError, ControlError):
        return "unreachable"
    return role_code(info, roles, login, role)


def _read_membership(store) -> tuple[Mapping, Sequence[str]]:
    with store.read() as reader:
        reader.ping()
        return reader.memberships()


def _config_code() -> str | None:
    try:
        load_config()
    except DashboardConfigError:
        return "malformed"
    return None


def run(*, store, control, password_hash: str, secret: str, config_dir: Path) -> list[Result]:
    """The six checks, in the contract's order. Nothing is written."""
    return [
        Result("database_read", _database(lambda: _read_membership(store), READ_LOGIN, READ_ROLE)),
        Result("database_control", _database(control.memberships, CONTROL_LOGIN, CONTROL_ROLE)),
        Result("password_hash", password_hash_code(password_hash)),
        Result("session_secret", session_secret_code(secret)),
        Result("schedule", "unreadable" if deployed.run_while_paused(config_dir) is None else None),
        Result("config", _config_code()),
    ]
