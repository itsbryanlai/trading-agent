"""The client's address, used as the key of the per-address sign-in delay (research D8).

The key is the last entry of `X-Forwarded-For`, the one Railway's edge proxy appends; a
client can forge the entries to its left but not that one. With no such header (local runs,
tests) it is the socket peer. uvicorn's own proxy-header handling stays off. Only the
secondary, per-address delay depends on this: the site-wide delay holds either way.
"""

from __future__ import annotations

from collections.abc import Mapping

MAX_LENGTH = 64
_HEADER = "x-forwarded-for"


def client_address(headers: Mapping[str, str], peer: str | None) -> str:
    forwarded = next((v for k, v in headers.items() if k.lower() == _HEADER), "")
    last = forwarded.rsplit(",", 1)[-1].strip()
    return (last or peer or "unknown")[:MAX_LENGTH]
