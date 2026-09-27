"""Suite-wide guard: no test may reach any host but this machine (specs/003-execution E14).

No test ever talks to the broker, not even the paper account (FR-019, CLAUDE.md).
This makes that structural: name resolution and socket connects to anything but
localhost fail the test. Postgres is reached through libpq, not Python sockets,
and on localhost anyway, so the integration suite is unaffected.
"""

from __future__ import annotations

import socket

import pytest

_LOCAL = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


class NetworkBlocked(RuntimeError):
    pass


def _check(host) -> None:
    if isinstance(host, bytes):
        host = host.decode()
    if host not in _LOCAL:
        raise NetworkBlocked(f"tests may not reach {host!r}; use tests/fakes/broker.py")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address[0])
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address[0])
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        _check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
