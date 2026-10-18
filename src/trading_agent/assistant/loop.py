"""The start-up drain, the poll loop and the sending rules (research A2-A5).

Updates are handled one at a time, in order. The next poll's offset confirms what was
handled, so an update is confirmed once its reply was sent or abandoned. Nothing here logs a
message, a reply, a chat ID or an exception's text: Telegram's carry the token's URL (A1).
`respond(update)` is the message handling; `sleep` and `stop` (anything with `is_set()`) are
injected.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from trading_agent.assistant import replies
from trading_agent.assistant.inbox import authorised, count_skippable
from trading_agent.assistant.telegram import (
    Conflict,
    FloodControl,
    TelegramError,
    TokenRejected,
)

log = logging.getLogger("trading_agent.assistant")

ALLOWED_UPDATES = ["message", "edited_message"]
DRAIN_LIMIT = 100
POLL_LIMIT = 100
CONFLICT_WAIT = 10
POLL_FLOOD_CAP = 300
SEND_FLOOD_CAP = 60
SEND_RETRY_WAIT = 2
BACKOFF_CAP = 60


class DatabaseLost(Exception):
    """`max_consecutive_failures` questions in a row could not read the database."""


def drain(telegram, *, chat_id: int, sleep: Callable[[float], None], stop) -> int:
    """Confirm every waiting update and answer none; say so once if any were the owner's.

    Returns the offset to poll from.
    """
    offset = 0
    skipped = 0
    while not stop.is_set():
        batch = _fetch(telegram, offset, 0, DRAIN_LIMIT, sleep, stop)
        ids = _ids(batch)
        if not ids:
            break
        skipped += count_skippable(batch, chat_id)
        offset = max(ids) + 1
    if skipped > 0:
        log.info("assistant: skipped %d waiting message(s)", skipped)
        send(telegram, chat_id, [replies.skipped(skipped)], sleep)
    return offset


def poll(
    telegram,
    *,
    chat_id: int,
    offset: int,
    long_poll_seconds: int,
    max_failures: int,
    respond: Callable,
    sleep: Callable[[float], None],
    stop,
) -> None:
    """Answer until `stop` is set. The current message finishes first."""
    confirmed = offset
    failures = 0
    try:
        while not stop.is_set():
            batch = _fetch(telegram, offset, long_poll_seconds, POLL_LIMIT, sleep, stop)
            ignored = 0
            for update in batch:
                uid = update.get("update_id")
                if not isinstance(uid, int) or isinstance(uid, bool):
                    continue
                if not authorised(update, chat_id):
                    ignored += 1
                    offset = max(offset, uid + 1)
                    continue
                handled = respond(update)
                send(telegram, chat_id, handled.replies, sleep)
                offset = max(offset, uid + 1)
                failures = _count_database(handled.outcome, failures, max_failures)
                if stop.is_set():
                    break
            if ignored:
                log.info("assistant: ignored %d update(s) from other chats", ignored)
    except DatabaseLost:
        _confirm(telegram, offset, confirmed)
        raise
    _confirm(telegram, offset, confirmed)


def run(
    telegram,
    *,
    chat_id: int,
    long_poll_seconds: int,
    max_failures: int,
    respond: Callable,
    sleep: Callable[[float], None],
    stop,
) -> None:
    offset = drain(telegram, chat_id=chat_id, sleep=sleep, stop=stop)
    poll(
        telegram,
        chat_id=chat_id,
        offset=offset,
        long_poll_seconds=long_poll_seconds,
        max_failures=max_failures,
        respond=respond,
        sleep=sleep,
        stop=stop,
    )


def send(telegram, chat_id: int, parts: list[str], sleep: Callable[[float], None]) -> None:
    """Send the parts in order (A2). A part that still fails ends the reply; none is repeated."""
    for number, text in enumerate(parts, start=1):
        if not _send_part(telegram, chat_id, text, sleep):
            log.warning("assistant: send abandoned at part %d/%d", number, len(parts))
            return


def _send_part(telegram, chat_id: int, text: str, sleep) -> bool:
    for attempt in (1, 2):
        try:
            telegram.send_message(chat_id, text)
            return True
        except TokenRejected:
            raise
        except TelegramError as exc:
            wait = (
                min(exc.retry_after, SEND_FLOOD_CAP)
                if isinstance(exc, FloodControl)
                else SEND_RETRY_WAIT
            )
            if attempt == 2:
                log.warning("assistant: telegram sendMessage failed: %s", type(exc).__name__)
                return False
            log.warning(
                "assistant: telegram sendMessage failed: %s, retry in %ds", type(exc).__name__, wait
            )
            sleep(wait)
    return False


def _fetch(telegram, offset: int, timeout: int, limit: int, sleep, stop) -> list[dict]:
    backoff = 1
    while not stop.is_set():
        try:
            return telegram.get_updates(offset, timeout, ALLOWED_UPDATES, limit)
        except TokenRejected:
            raise
        except TelegramError as exc:
            if isinstance(exc, Conflict):
                wait = CONFLICT_WAIT
            elif isinstance(exc, FloodControl):
                wait = min(exc.retry_after, POLL_FLOOD_CAP)
            else:
                wait = backoff
                backoff = min(backoff * 2, BACKOFF_CAP)
            log.warning(
                "assistant: telegram getUpdates failed: %s, retry in %ds", type(exc).__name__, wait
            )
            sleep(wait)
    return []


def _ids(batch: list[dict]) -> list[int]:
    return [
        u["update_id"]
        for u in batch
        if isinstance(u.get("update_id"), int) and not isinstance(u.get("update_id"), bool)
    ]


def _count_database(outcome, failures: int, max_failures: int) -> int:
    if outcome.kind != "database":
        return 0
    failures += 1
    log.error(
        "assistant: database failed: %s (consecutive %d/%d)",
        outcome.detail or "unknown", failures, max_failures,
    )
    if failures >= max_failures:
        raise DatabaseLost("database failed repeatedly")
    return failures


def _confirm(telegram, offset: int, confirmed: int) -> None:
    """Best effort: confirm the last handled update so a restart doesn't count it as skipped."""
    if offset <= confirmed:
        return
    try:
        telegram.get_updates(offset, 0, ALLOWED_UPDATES, 1)
    except TelegramError:
        pass
