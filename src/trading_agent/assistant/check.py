"""`--check` and `--find-chat-id` (research A18).

`--check` prints one line per check and sends nothing to the chat. `--find-chat-id` needs only
the bot token: it polls for a fixed time, prints each message's chat id and type to stdout
(the owner's own terminal), replies to nothing, and advances its offset so nothing prints
twice. A failure names the check and the exception type, never the message: Telegram
exceptions can carry the token's URL.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent.assistant import settings_view
from trading_agent.assistant.answer import ANSWER_SCHEMA, PLAN_SCHEMA, parse_answer, parse_plan
from trading_agent.assistant.menu import ENTRIES
from trading_agent.assistant.prompt import (
    ANSWER_SYSTEM,
    PLAN_SYSTEM,
    answer_document,
    plan_document,
)
from trading_agent.assistant.telegram import TokenRejected

FIND_SECONDS = 120
POLL_SECONDS = 30
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


class UnusablePlan(Exception):
    """The model's answer did not parse as a plan."""


class UnusableAnswer(Exception):
    """The model's answer did not parse as a reply."""


class WebhookSet(Exception):
    """The bot has a webhook, so getUpdates conflicts (409). The URL is never shown."""

    code = "webhook_set"


class Unavailable(Exception):
    """A settings section or key could not be read."""


def run_checks(
    telegram,
    store,
    plan_model,
    answer_model,
    *,
    zone_name: str,
    config_dir: Path,
    now: datetime,
    out: Callable[[str], None] = print,
) -> int:
    """Run every check, print one line each, and return 0 or 1."""
    failed = False
    for name, check in (
        ("telegram", lambda: telegram.get_me()),
        ("telegram_webhook", lambda: _no_webhook(telegram)),
        ("database", lambda: store.known_symbols()),
        ("model_plan", lambda: _model_plan(plan_model, zone_name, now)),
        ("model_answer", lambda: _model_answer(answer_model, zone_name, now)),
        ("time_zone", lambda: ZoneInfo(zone_name)),
        ("settings", lambda: _settings(config_dir)),
    ):
        try:
            check()
        except Exception as exc:
            failed = True
            out(f"{name}: FAILED {getattr(exc, 'code', type(exc).__name__)}")
        else:
            out(f"{name}: ok")
    return EXIT_FAILED if failed else EXIT_OK


def _no_webhook(telegram) -> None:
    if telegram.get_webhook_info().get("url") != "":
        raise WebhookSet


def _model_plan(plan_model, zone_name: str, now: datetime) -> None:
    document = plan_document(now.astimezone(ZoneInfo(zone_name)), "hello", [], ENTRIES)
    reply = plan_model.complete(PLAN_SYSTEM, document, PLAN_SCHEMA)
    if parse_plan(reply.text) is None:
        raise UnusablePlan


def _model_answer(answer_model, zone_name: str, now: datetime) -> None:
    document = answer_document(now.astimezone(ZoneInfo(zone_name)), "hello", [], [], [], True)
    reply = answer_model.complete(ANSWER_SYSTEM, document, ANSWER_SCHEMA)
    if parse_answer(reply.text) is None:
        raise UnusableAnswer


def _settings(config_dir: Path) -> None:
    if _has_unavailable(settings_view.read(config_dir, None)):
        raise Unavailable


def _has_unavailable(value) -> bool:
    if isinstance(value, dict):
        return any(_has_unavailable(v) for v in value.values())
    return value == settings_view.UNAVAILABLE


def find_chat_id(
    telegram,
    *,
    clock: Callable[[], float] = time.monotonic,
    out: Callable[[str], None] = print,
) -> int:
    """Print `chat.id=<id> chat.type=<type>` for each message received in `FIND_SECONDS`."""
    started = clock()
    offset = 0
    try:
        while (remaining := FIND_SECONDS - (clock() - started)) > 0:
            timeout = max(1, min(POLL_SECONDS, int(remaining)))
            for update in telegram.get_updates(offset, timeout, ["message"], 100):
                offset = max(offset, _int(update.get("update_id")) + 1)
                message = update.get("message")
                chat = message.get("chat") if isinstance(message, dict) else None
                if isinstance(chat, dict) and "id" in chat:
                    out(f"chat.id={chat['id']} chat.type={chat.get('type')}")
    except TokenRejected as exc:
        out(f"telegram: FAILED {type(exc).__name__}")
        return EXIT_REFUSED
    except Exception as exc:
        out(f"telegram: FAILED {type(exc).__name__}")
        return EXIT_FAILED
    return EXIT_OK


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
