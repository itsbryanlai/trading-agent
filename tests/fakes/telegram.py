"""A fake Telegram: queued updates, recorded sends and offsets (specs/013 T003).

Implements the three methods of `assistant.telegram.TelegramClient`. `get_updates` confirms
updates the way Telegram does: those below `offset` are dropped.
"""

from __future__ import annotations


class FakeTelegram:
    def __init__(self, updates: list[dict] | None = None, me: dict | None = None) -> None:
        self.queue: list[dict] = list(updates or [])
        self.sent: list[tuple[int, str]] = []
        self.offsets: list[int] = []
        self.me = me or {"id": 1, "is_bot": True, "username": "fake_bot"}
        self.fail_send: list[Exception] = []  # raised by the next send_message calls, in order
        self.fail_updates: list[Exception] = []

    def push(self, update: dict) -> None:
        self.queue.append(update)

    def get_updates(
        self, offset: int, timeout: int, allowed_updates: list[str], limit: int
    ) -> list[dict]:
        self.offsets.append(offset)
        if self.fail_updates:
            raise self.fail_updates.pop(0)
        self.queue = [u for u in self.queue if u["update_id"] >= offset]
        return list(self.queue[:limit])

    def send_message(self, chat_id: int, text: str) -> None:
        if self.fail_send:
            raise self.fail_send.pop(0)
        self.sent.append((chat_id, text))

    def get_me(self) -> dict:
        return dict(self.me)
