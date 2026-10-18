"""Sign-in delays: "not before" gates, site-wide and per address (research D7).

Pure: the clock is an argument. Nothing sleeps. An attempt that arrives too early is told how
long to wait and is neither checked nor counted. An attempt that is allowed *reserves* the
single check slot under the lock, so at most one password check runs at a time whatever the
load, and `release` frees it (the caller always releases, in a `finally`). There is no
lockout: once the wait has passed, an attempt is always checked.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Acquired:
    """The caller holds the check slot and must call `release`."""


@dataclass(frozen=True)
class Wait:
    """Too soon: try again in this many whole seconds (at least 1)."""

    seconds: int


@dataclass
class _Gate:
    failures: int = 0
    not_before: datetime | None = None


class SignInGate:
    def __init__(self, first_seconds: int, cap_seconds: int, tracked_addresses: int) -> None:
        self._first = first_seconds
        self._cap = cap_seconds
        self._tracked = tracked_addresses
        self._site = _Gate()
        self._addresses: OrderedDict[str, _Gate] = OrderedDict()
        self._in_flight = False
        self._lock = threading.Lock()

    @property
    def tracked(self) -> int:
        with self._lock:
            return len(self._addresses)

    def _delay(self, failures: int) -> float:
        return min(self._first * 2 ** (failures - 1), self._cap)

    @staticmethod
    def _remaining(gate: _Gate | None, now: datetime) -> float:
        if gate is None or gate.not_before is None:
            return 0.0
        return max((gate.not_before - now).total_seconds(), 0.0)

    def try_acquire(self, address: str, now: datetime) -> Acquired | Wait:
        with self._lock:
            remaining = self._remaining(self._site, now) + self._remaining(
                self._addresses.get(address), now
            )
            if remaining > 0:
                return Wait(max(math.ceil(remaining), 1))
            if self._in_flight:
                return Wait(1)
            self._in_flight = True
            return Acquired()

    def release(self, address: str, ok: bool, now: datetime) -> None:
        with self._lock:
            self._in_flight = False
            if ok:
                self._site = _Gate()
                self._addresses.pop(address, None)
                return
            self._fail(self._site, now)
            gate = self._addresses.pop(address, None) or _Gate()
            self._fail(gate, now)
            self._addresses[address] = gate  # newest last
            while len(self._addresses) > self._tracked:
                self._addresses.popitem(last=False)

    def _fail(self, gate: _Gate, now: datetime) -> None:
        gate.failures += 1
        delay = self._delay(gate.failures)
        gate.not_before = now + timedelta(seconds=delay)
