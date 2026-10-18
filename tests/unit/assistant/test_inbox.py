"""Authorisation and classification (specs/013 tasks T004; research A5, A6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.assistant.inbox import (
    ActionRequest,
    Command,
    Question,
    TextOnly,
    authorised,
    classify,
    count_skippable,
)

OWNER = 4242
WORDS = ["pause", "resume", "unpause", "halt", "buy", "sell", "cancel", "trade", "liquidate"]


def msg(text="hi", chat_id=OWNER, chat_type="private", **extra):
    return {"message_id": 1, "chat": {"id": chat_id, "type": chat_type}, "text": text, **extra}


def upd(message=None, key="message", update_id=1):
    return {"update_id": update_id, key: message if message is not None else msg()}


def test_the_owners_private_message_and_edit_are_authorised():
    assert authorised(upd(), OWNER)
    assert authorised(upd(key="edited_message"), OWNER)


@pytest.mark.parametrize(
    "update",
    [
        upd(msg(chat_id=-100123, chat_type="group")),
        upd(msg(chat_id=OWNER, chat_type="group")),
        upd(msg(chat_id=OWNER, chat_type="supergroup")),
        upd(msg(chat_id=OWNER, chat_type="channel")),
        upd(msg(chat_id=999, chat_type="private")),
        upd(msg(chat_id=True, chat_type="private")),
        upd(msg(chat_id=str(OWNER))),
        {"update_id": 1, "channel_post": msg()},
        {"update_id": 1, "callback_query": {"id": "1"}},
        {"update_id": 1},
        {"update_id": 1, "message": "text"},
        {"update_id": 1, "message": {"text": "x"}},
        {"update_id": 1, "message": {"chat": "x"}},
        "not a dict",
        None,
    ],
)
def test_everything_else_is_not_authorised(update):
    assert not authorised(update, OWNER)


def test_an_update_with_an_unauthorised_part_is_not_authorised():
    update = {"update_id": 1, "message": msg(), "edited_message": msg(chat_id=1)}
    assert not authorised(update, OWNER)


@given(st.integers().filter(lambda n: n != OWNER), st.sampled_from(["group", "private", "channel"]))
def test_a_chat_that_is_not_the_owners_is_never_authorised(chat_id, kind):
    assert not authorised(upd(msg(chat_id=chat_id, chat_type=kind)), OWNER)


def test_count_skippable_counts_only_messages_from_the_owner():
    updates = [
        upd(),
        upd(key="edited_message"),
        upd(msg(chat_id=1)),
        upd(msg(chat_type="group")),
        upd(),
        {"update_id": 9, "my_chat_member": {}},
        "junk",
    ]
    assert count_skippable(updates, OWNER) == 2
    assert count_skippable([], OWNER) == 0


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        (upd(msg("Why did the PM sell MU?")), Question("Why did the PM sell MU?")),
        (upd(msg("  how is my portfolio?  ")), Question("how is my portfolio?")),
        (upd(msg("Pause!")), ActionRequest("pause")),
        (upd(msg("SELL AAPL")), ActionRequest("sell")),
        (upd(msg("please pause trading")), Question("please pause trading")),
        (upd(msg("/start")), Command()),
        (upd(msg("/pause")), Command()),
        (upd(msg("  /help me")), Command()),
        (upd(msg("Pause!"), key="edited_message"), TextOnly()),
        (upd(msg("hello", forward_origin={"type": "user"})), TextOnly()),
        (upd(msg("pause", forward_origin={"type": "user"})), TextOnly()),
        (upd({"message_id": 1, "chat": {"id": OWNER, "type": "private"}, "photo": []}), TextOnly()),
        (upd(msg("")), TextOnly()),
        (upd(msg("   ")), TextOnly()),
        (upd(msg(None)), TextOnly()),
        ({"update_id": 1}, TextOnly()),
    ],
)
def test_classify_follows_the_order(update, expected):
    assert classify(update, WORDS) == expected


def test_a_command_beats_an_action_word():
    assert classify(upd(msg("/pause")), WORDS) == Command()
