"""Who is answered, and what an authorised message is (research A5, A6).

Pure. `authorised` is the only gate on who gets a reply; `classify` applies A6's order.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.assistant.actions import match


@dataclass(frozen=True)
class TextOnly:
    """An edit, a forward, or a message without text: gets the text-only reply."""


@dataclass(frozen=True)
class Command:
    """Text starting with `/`: gets the help reply."""


@dataclass(frozen=True)
class ActionRequest:
    matched_word: str


@dataclass(frozen=True)
class Question:
    text: str


def _messages(update) -> list:
    if not isinstance(update, dict):
        return []
    return [update[key] for key in ("message", "edited_message") if key in update]


def _from_chat(message, chat_id: int) -> bool:
    chat = message.get("chat") if isinstance(message, dict) else None
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return False
    found = chat.get("id")
    return isinstance(found, int) and not isinstance(found, bool) and found == chat_id


def authorised(update, chat_id: int) -> bool:
    """True only for a message or edited message in the owner's private chat."""
    messages = _messages(update)
    return bool(messages) and all(_from_chat(message, chat_id) for message in messages)


def count_skippable(updates, chat_id: int) -> int:
    """The `message`s from the authorised chat (A4); edits and other chats don't count."""
    return sum(
        1
        for update in updates
        if isinstance(update, dict) and "message" in update and authorised(update, chat_id)
    )


def classify(update, action_words) -> TextOnly | Command | ActionRequest | Question:
    """A6's order. Assumes `authorised` already held."""
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict) or "forward_origin" in message:
        return TextOnly()
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return TextOnly()
    if text.lstrip().startswith("/"):
        return Command()
    word = match(text, action_words)
    if word is not None:
        return ActionRequest(word)
    return Question(text.strip())
