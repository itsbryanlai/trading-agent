"""Conversation memory (research A13, FR-018): an in-process deque, never stored.

Fixed replies and refusals are not added by the caller, so a refusal never becomes part of a
later question's context. The clock is an argument.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Exchange:
    asked_at: datetime
    question: str
    reply: str


class Memory:
    def __init__(self, max_exchanges: int, idle_minutes: int) -> None:
        self._idle = timedelta(minutes=idle_minutes)
        self._items: deque[Exchange] = deque(maxlen=max_exchanges)

    def add(self, asked_at: datetime, question: str, reply: str) -> None:
        if self._items.maxlen == 0:
            return
        self._items.append(Exchange(asked_at, question, reply))

    def recent(self, now: datetime) -> list[Exchange]:
        """The remembered exchanges, oldest first; everything goes once the newest is idle."""
        if self._items and now - self._items[-1].asked_at > self._idle:
            self._items.clear()
        return list(self._items)
