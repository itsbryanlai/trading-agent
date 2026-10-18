"""Sign-in primitives: scrypt password hashes, signed cookies, sessions, CSRF (research D4-D6, D11).

Pure: the clock is an argument, nothing reads the environment, and nothing touches the network
or database. Sessions live in memory only; a restart empties them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_MAX_N = 2**20  # bounds what a stored hash can make verify allocate
_MAX_R = 32
_MAX_P = 16
_TOKEN_BYTES = 32


class MalformedHash(ValueError):
    """The stored hash is not `scrypt$n$r$p$<salt b64>$<hash b64>` with sane parameters."""


@dataclass(frozen=True)
class ParsedHash:
    n: int
    r: int
    p: int
    salt: bytes
    digest: bytes


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_DKLEN,
        maxmem=128 * r * (n + p + 2) + 2**20,
    )


def hash_password(password: str, *, salt: bytes | None = None, n: int = SCRYPT_N) -> str:
    salt = secrets.token_bytes(_SALT_BYTES) if salt is None else salt
    digest = _scrypt(password, salt, n, SCRYPT_R, SCRYPT_P)
    return "$".join(
        (
            "scrypt",
            str(n),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def _int(text: str, high: int) -> int:
    if not text.isascii() or not text.isdigit():
        raise MalformedHash("not a number")
    value = int(text)
    if not 1 <= value <= high:
        raise MalformedHash("out of range")
    return value


def _b64(text: str) -> bytes:
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise MalformedHash("not base64") from exc
    if not raw:
        raise MalformedHash("empty")
    return raw


def parse_hash(stored: str) -> ParsedHash:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        raise MalformedHash("wrong shape")
    n = _int(parts[1], _MAX_N)
    if n < 2 or n & (n - 1):
        raise MalformedHash("n is not a power of two")
    return ParsedHash(
        n=n,
        r=_int(parts[2], _MAX_R),
        p=_int(parts[3], _MAX_P),
        salt=_b64(parts[4]),
        digest=_b64(parts[5]),
    )


def verify_password(password: str, stored: str) -> bool:
    """True only for the password that made `stored`. A malformed hash is false, never an error."""
    try:
        parsed = parse_hash(stored)
        actual = _scrypt(password, parsed.salt, parsed.n, parsed.r, parsed.p)
    except (MalformedHash, ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, parsed.digest)


# --- signed cookie value ---------------------------------------------------------------------


def _signature(secret: str, session_id: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def sign(secret: str, session_id: str) -> str:
    """The cookie value: `<id>.<HMAC-SHA256 signature, base64url>`."""
    return f"{session_id}.{_signature(secret, session_id)}"


def unsign(secret: str, value: str) -> str | None:
    """The session id inside a cookie value this secret signed, else None."""
    session_id, dot, signature = value.rpartition(".")
    if not dot or not session_id or not signature:
        return None
    expected = _signature(secret, session_id)
    if not hmac.compare_digest(expected.encode("utf-8"), signature.encode("utf-8")):
        return None
    return session_id


# --- sessions --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    id: str
    created_at: datetime
    expires_at: datetime
    csrf: str


class Sessions:
    """Live sessions in memory: at most `max_sessions`, each valid until it expires."""

    def __init__(self, max_sessions: int, lifetime: timedelta) -> None:
        self._max = max_sessions
        self._lifetime = lifetime
        self._by_id: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self, now: datetime) -> None:
        for session_id in [i for i, s in self._by_id.items() if now >= s.expires_at]:
            del self._by_id[session_id]

    def create(self, now: datetime) -> Session:
        session = Session(
            id=secrets.token_urlsafe(_TOKEN_BYTES),
            created_at=now,
            expires_at=now + self._lifetime,
            csrf=secrets.token_urlsafe(_TOKEN_BYTES),
        )
        with self._lock:
            self._purge(now)
            self._by_id[session.id] = session
            while len(self._by_id) > self._max:
                self._by_id.popitem(last=False)
        return session

    def get(self, session_id: str, now: datetime) -> Session | None:
        with self._lock:
            session = self._by_id.get(session_id)
            if session is None:
                return None
            if now >= session.expires_at:
                del self._by_id[session_id]
                return None
            return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._by_id.pop(session_id, None)


def csrf_ok(session: Session | None, submitted: str | None) -> bool:
    """True only when `submitted` is exactly the session's CSRF value."""
    if session is None or not submitted:
        return False
    return hmac.compare_digest(session.csrf.encode("utf-8"), submitted.encode("utf-8"))
