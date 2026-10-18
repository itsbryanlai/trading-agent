"""The drain, the poll loop, the sending rules and the error policy
(specs/013 tasks T013; research A2-A5)."""

from __future__ import annotations

import logging
import threading

import pytest

from tests.fakes.telegram import FakeTelegram
from trading_agent.assistant import loop, replies
from trading_agent.assistant.service import Handled, Outcome
from trading_agent.assistant.telegram import (
    Conflict,
    FloodControl,
    TelegramUnavailable,
    TokenRejected,
)

OWNER = 4242
STRANGER = 999


def message(update_id, text="hi", chat_id=OWNER, chat_type="private"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


class Sleeps:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds):
        self.calls.append(seconds)


class StopWhenEmpty(FakeTelegram):
    """Sets `stop` once a poll returns nothing, so a test's loop ends."""

    def __init__(self, stop, *args, **kw):
        super().__init__(*args, **kw)
        self.stop = stop
        self.arguments: list[tuple] = []

    def get_updates(self, offset, timeout, allowed_updates, limit):
        self.arguments.append((offset, timeout, allowed_updates, limit))
        got = super().get_updates(offset, timeout, allowed_updates, limit)
        if not got and not self.fail_updates:
            self.stop.set()
        return got


def answered(*parts, kind="answered", detail=""):
    return Handled(list(parts), Outcome(kind, detail=detail))


def drive(updates, respond=None, *, telegram=None, sleep=None, max_failures=3, offset=1):
    stop = threading.Event()
    telegram = telegram or StopWhenEmpty(stop, updates)
    telegram.stop = stop
    sleep = sleep or Sleeps()
    seen: list[dict] = []

    def default(update):
        seen.append(update)
        return answered(f"re:{update['message']['text']}")

    loop.poll(
        telegram,
        chat_id=OWNER,
        offset=offset,
        long_poll_seconds=50,
        max_failures=max_failures,
        respond=respond or default,
        sleep=sleep,
        stop=stop,
    )
    return telegram, seen, sleep


# --- the drain ---------------------------------------------------------------------------


def test_the_drain_confirms_everything_answers_nothing_and_says_so_once(caplog):
    telegram = FakeTelegram([message(n) for n in range(1, 251)])
    sleeps = Sleeps()
    with caplog.at_level(logging.INFO, logger="trading_agent.assistant"):
        offset = loop.drain(telegram, chat_id=OWNER, sleep=sleeps, stop=threading.Event())
    assert offset == 251 and telegram.queue == []
    assert telegram.sent == [(OWNER, replies.skipped(250))]
    assert "assistant: skipped 250 waiting message(s)" in caplog.text


def test_the_drain_sends_nothing_when_nothing_was_waiting():
    telegram = FakeTelegram()
    assert loop.drain(telegram, chat_id=OWNER, sleep=Sleeps(), stop=threading.Event()) == 0
    assert telegram.sent == []


def test_the_drain_counts_only_the_owners_messages():
    other = message(2, chat_id=STRANGER)
    edit = {"update_id": 3, "edited_message": message(3)["message"]}
    telegram = FakeTelegram([message(1), other, edit])
    loop.drain(telegram, chat_id=OWNER, sleep=Sleeps(), stop=threading.Event())
    assert telegram.sent == [(OWNER, replies.skipped(1))]
    telegram = FakeTelegram([other, edit])
    loop.drain(telegram, chat_id=OWNER, sleep=Sleeps(), stop=threading.Event())
    assert telegram.sent == []


def test_the_drain_uses_a_zero_timeout_and_the_allowed_update_types():
    stop = threading.Event()
    telegram = StopWhenEmpty(threading.Event(), [message(1)])
    loop.drain(telegram, chat_id=OWNER, sleep=Sleeps(), stop=stop)
    assert all(a[1] == 0 and a[2] == ["message", "edited_message"] for a in telegram.arguments)


# --- the poll loop -----------------------------------------------------------------------


def test_updates_are_answered_in_order_and_the_offset_confirms_them():
    telegram, seen, _ = drive([message(5, "one"), message(6, "two")], offset=5)
    assert [u["update_id"] for u in seen] == [5, 6]
    assert telegram.sent == [(OWNER, "re:one"), (OWNER, "re:two")]
    assert telegram.queue == []
    assert telegram.arguments[0] == (5, 50, ["message", "edited_message"], 100)
    assert telegram.offsets[-1] == 7


def test_other_chats_are_ignored_without_a_reply_and_logged_once_per_batch(caplog):
    batch = [
        message(1, "mine"),
        message(2, chat_id=STRANGER),
        message(3, chat_id=STRANGER, chat_type="group"),
        message(4, chat_id=-100),
    ]
    with caplog.at_level(logging.INFO, logger="trading_agent.assistant"):
        telegram, seen, _ = drive(batch, offset=1)
    assert [u["update_id"] for u in seen] == [1]
    assert telegram.sent == [(OWNER, "re:mine")]
    assert telegram.queue == []  # the ignored ones were confirmed too
    lines = [r.getMessage() for r in caplog.records if "ignored" in r.getMessage()]
    assert lines == ["assistant: ignored 3 update(s) from other chats"]
    assert str(STRANGER) not in caplog.text and str(OWNER) not in caplog.text


def test_a_reply_goes_out_in_parts_in_order():
    def respond(update):
        return answered("part one", "part two", "part three")

    telegram, *_ = drive([message(1)], respond)
    assert [t for _, t in telegram.sent] == ["part one", "part two", "part three"]


def test_sigterm_finishes_the_current_message_then_returns():
    stop = threading.Event()
    telegram = StopWhenEmpty(stop, [message(1, "a"), message(2, "b"), message(3, "c")])
    handled: list[int] = []

    def respond(update):
        handled.append(update["update_id"])
        stop.set()  # SIGTERM arrives while the message is being answered
        return answered("done")

    loop.poll(
        telegram,
        chat_id=OWNER,
        offset=1,
        long_poll_seconds=50,
        max_failures=3,
        respond=respond,
        sleep=Sleeps(),
        stop=stop,
    )
    assert handled == [1]
    assert telegram.sent == [(OWNER, "done")]  # the reply was still sent
    assert [u["update_id"] for u in telegram.queue] == [2, 3]  # waiting, not lost
    assert telegram.offsets[-1] == 2  # the handled one is confirmed


def test_run_drains_then_answers_only_new_messages():
    stop = threading.Event()

    class Telegram(FakeTelegram):
        def get_updates(self, offset, timeout, allowed_updates, limit):
            got = super().get_updates(offset, timeout, allowed_updates, limit)
            if timeout == 0 and not got and not self.drained:
                self.drained = True
                self.push(message(3, "fresh"))
            elif timeout != 0 and not got:
                stop.set()
            return got

    telegram = Telegram([message(1), message(2)])
    telegram.drained = False
    loop.run(
        telegram,
        chat_id=OWNER,
        long_poll_seconds=50,
        max_failures=3,
        respond=lambda u: answered("re:" + u["message"]["text"]),
        sleep=Sleeps(),
        stop=stop,
    )
    assert telegram.sent == [(OWNER, replies.skipped(2)), (OWNER, "re:fresh")]


# --- sending (A2) ------------------------------------------------------------------------


def test_a_429_on_a_part_waits_retry_after_and_retries_that_part_once():
    telegram = FakeTelegram()
    telegram.fail_send = [FloodControl(7)]
    sleeps = Sleeps()
    loop.send(telegram, OWNER, ["a", "b"], sleeps)
    assert sleeps.calls == [7]
    assert [t for _, t in telegram.sent] == ["a", "b"]


def test_a_send_flood_wait_is_capped_at_60_seconds():
    telegram = FakeTelegram()
    telegram.fail_send = [FloodControl(500)]
    sleeps = Sleeps()
    loop.send(telegram, OWNER, ["a"], sleeps)
    assert sleeps.calls == [60]


def test_a_network_failure_retries_the_part_once_after_two_seconds():
    telegram = FakeTelegram()
    telegram.fail_send = [TelegramUnavailable("telegram: OSError")]
    sleeps = Sleeps()
    loop.send(telegram, OWNER, ["a"], sleeps)
    assert sleeps.calls == [2]
    assert [t for _, t in telegram.sent] == ["a"]


def test_a_part_that_still_fails_abandons_the_rest_and_the_update_is_confirmed(caplog):
    telegram_stop = threading.Event()
    telegram = StopWhenEmpty(telegram_stop, [message(1)])
    telegram.fail_send = [TelegramUnavailable("x"), TelegramUnavailable("x")]
    with caplog.at_level(logging.WARNING, logger="trading_agent.assistant"):
        drive([], lambda u: answered("p1", "p2", "p3"), telegram=telegram)
    assert telegram.sent == []  # part 1 failed twice; parts 2 and 3 were not tried
    assert "assistant: send abandoned at part 1/3" in caplog.text
    assert telegram.queue == []  # confirmed anyway


def test_a_later_part_failing_leaves_the_sent_parts_alone_and_never_repeats_one():
    telegram = FakeTelegram()
    sleeps = Sleeps()
    original = telegram.send_message
    attempts: list[str] = []

    def flaky(chat_id, text):
        attempts.append(text)
        if text == "p2":
            raise TelegramUnavailable("x")
        original(chat_id, text)

    telegram.send_message = flaky
    loop.send(telegram, OWNER, ["p1", "p2", "p3"], sleeps)
    assert [t for _, t in telegram.sent] == ["p1"]
    assert attempts == ["p1", "p2", "p2"]  # p1 once, p2 retried once, p3 never tried
    assert sleeps.calls == [2]


def test_no_part_is_ever_sent_twice_when_each_first_attempt_fails():
    telegram = FakeTelegram()
    first: set[str] = set()
    original = telegram.send_message

    def flaky(chat_id, text):
        if text not in first:
            first.add(text)
            raise FloodControl(1) if text == "a" else TelegramUnavailable("x")
        original(chat_id, text)

    telegram.send_message = flaky
    loop.send(telegram, OWNER, ["a", "b", "c"], Sleeps())
    assert [t for _, t in telegram.sent] == ["a", "b", "c"]


def test_a_rejected_token_while_sending_propagates():
    telegram = FakeTelegram()
    telegram.fail_send = [TokenRejected("telegram: HTTP 401")]
    with pytest.raises(TokenRejected):
        loop.send(telegram, OWNER, ["a"], Sleeps())


# --- errors while polling ----------------------------------------------------------------


def test_a_conflict_waits_ten_seconds_and_retries():
    telegram = StopWhenEmpty(threading.Event(), [message(1)])
    telegram.fail_updates = [Conflict("telegram: HTTP 409")]
    _, seen, sleeps = drive([], telegram=telegram)
    assert sleeps.calls == [10] and len(seen) == 1


def test_thirty_conflicts_in_a_row_log_critical_once_and_keep_retrying(caplog):
    caplog.set_level(logging.INFO)
    telegram = StopWhenEmpty(threading.Event(), [message(1)])
    telegram.fail_updates = [Conflict("telegram: HTTP 409")] * 35
    _, seen, sleeps = drive([], telegram=telegram)
    assert sleeps.calls == [10] * 35 and len(seen) == 1
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert [r.getMessage() for r in critical] == [
        "assistant: telegram getUpdates keeps conflicting (webhook set or another poller)"
    ]


def test_twenty_nine_conflicts_do_not_log_critical(caplog):
    telegram = StopWhenEmpty(threading.Event(), [message(1)])
    telegram.fail_updates = [Conflict("telegram: HTTP 409")] * 29
    drive([], telegram=telegram)
    assert not [r for r in caplog.records if r.levelno == logging.CRITICAL]


def test_a_new_conflict_streak_logs_again(caplog):
    telegram = StopWhenEmpty(threading.Event(), [message(1), message(2)])
    telegram.fail_updates = [Conflict("telegram: HTTP 409")] * 30
    first = telegram.get_updates

    def second_streak(offset, timeout, allowed_updates, limit):
        got = first(offset, timeout, allowed_updates, limit)
        if got and not telegram.fail_updates and offset == 1:
            # the confirming call after each of the two updates uses one
            telegram.fail_updates = [Conflict("telegram: HTTP 409")] * 32
        return got

    telegram.get_updates = second_streak
    drive([], telegram=telegram)
    assert len([r for r in caplog.records if r.levelno == logging.CRITICAL]) == 2


def test_a_flood_wait_on_polling_is_capped_at_300_seconds():
    telegram = StopWhenEmpty(threading.Event(), [message(1)])
    telegram.fail_updates = [FloodControl(30), FloodControl(9000)]
    _, _, sleeps = drive([], telegram=telegram)
    assert sleeps.calls == [30, 300]


def test_unavailable_backs_off_one_two_four_up_to_sixty():
    telegram = StopWhenEmpty(threading.Event(), [message(1)])
    telegram.fail_updates = [TelegramUnavailable("x")] * 9
    _, seen, sleeps = drive([], telegram=telegram)
    assert sleeps.calls == [1, 2, 4, 8, 16, 32, 60, 60, 60] and len(seen) == 1


def test_a_rejected_token_while_polling_raises():
    telegram = FakeTelegram()
    telegram.fail_updates = [TokenRejected("telegram: HTTP 401")]
    with pytest.raises(TokenRejected):
        loop.poll(
            telegram,
            chat_id=OWNER,
            offset=1,
            long_poll_seconds=50,
            max_failures=3,
            respond=lambda u: answered("x"),
            sleep=Sleeps(),
            stop=threading.Event(),
        )


def test_errors_are_logged_by_type_only(caplog):
    telegram = StopWhenEmpty(threading.Event(), [message(1)])
    telegram.fail_updates = [TelegramUnavailable("https://api.telegram.org/bot123:SECRET/x")]
    with caplog.at_level(logging.WARNING, logger="trading_agent.assistant"):
        drive([], telegram=telegram)
    assert "SECRET" not in caplog.text and "TelegramUnavailable" in caplog.text


# --- the database ------------------------------------------------------------------------


def database_down(update):
    return Handled([replies.database()], Outcome("database", detail="StoreError"))


def test_database_failures_in_a_row_raise_database_lost_after_replying(caplog):
    telegram = FakeTelegram([message(n) for n in (1, 2, 3, 4)])
    with caplog.at_level(logging.ERROR, logger="trading_agent.assistant"):
        with pytest.raises(loop.DatabaseLost):
            drive([], database_down, telegram=telegram, max_failures=3)
    assert len(telegram.sent) == 3  # every failed question was still answered
    assert "assistant: database failed: StoreError (consecutive 3/3)" in caplog.text


def test_a_good_answer_resets_the_database_count():
    outcomes = iter(
        [database_down, database_down, lambda u: answered("ok"), database_down, database_down]
    )

    def respond(update):
        return next(outcomes)(update)

    telegram = StopWhenEmpty(threading.Event(), [message(n) for n in range(1, 6)])
    drive([], respond, telegram=telegram, max_failures=3)  # does not raise
    assert len(telegram.sent) == 5


# --- a message that crashes the handler (T036, review L1) --------------------------------


def test_an_exception_from_respond_gets_the_fixed_reply_and_the_loop_continues(caplog):
    caplog.set_level(logging.INFO)
    private_text = "text-inside-the-exception"

    def respond(update):
        if update["update_id"] == 1:
            raise ValueError(private_text)
        return answered(f"re:{update['message']['text']}")

    telegram, _, _ = drive([message(1, "one"), message(2, "two")], respond)
    assert [text for _, text in telegram.sent] == [replies.internal_error(), "re:two"]
    assert "assistant: message failed: ValueError" in caplog.text
    assert private_text not in caplog.text
    assert [r.levelno for r in caplog.records if "message failed" in r.getMessage()] == [
        logging.ERROR
    ]


def test_a_rejected_token_or_lost_database_from_respond_still_propagates():
    for error in (TokenRejected(), loop.DatabaseLost("x")):

        def respond(update, error=error):
            raise error

        with pytest.raises(type(error)):
            drive([message(1)], respond)


# --- an update is confirmed right after its reply (T040, review L6) ----------------------


def test_each_update_is_confirmed_before_the_next_is_handled():
    stop = threading.Event()
    telegram = StopWhenEmpty(stop, [message(1), message(2)])
    confirmed_when_handled = []

    def respond(update):
        confirmed_when_handled.append(list(telegram.arguments))
        return answered("ok")

    drive([], respond, telegram=telegram)
    # the second message is handled only after getUpdates(2, 0, ..., 1) went out
    assert (2, 0, loop.ALLOWED_UPDATES, 1) in confirmed_when_handled[1]
    assert (1, 0, loop.ALLOWED_UPDATES, 1) not in confirmed_when_handled[0]


def test_a_crash_after_answering_update_one_leaves_it_confirmed():
    stop = threading.Event()
    telegram = StopWhenEmpty(stop, [message(1), message(2)])

    def respond(update):
        if update["update_id"] == 2:
            raise loop.DatabaseLost("x")
        return answered("ok")

    with pytest.raises(loop.DatabaseLost):
        drive([], respond, telegram=telegram)
    assert telegram.queue[0]["update_id"] == 2  # update 1 was confirmed, 2 was not (it crashed)
    # a restart's drain counts only what is still waiting
    loop.drain(telegram, chat_id=OWNER, sleep=Sleeps(), stop=threading.Event())
    assert [t for _, t in telegram.sent] == ["ok", replies.skipped(1)]


def test_a_failing_confirmation_is_logged_by_type_and_ignored(caplog):
    class Flaky(StopWhenEmpty):
        def get_updates(self, offset, timeout, allowed_updates, limit):
            if limit == 1 and timeout == 0:
                self.arguments.append((offset, timeout, allowed_updates, limit))
                raise TelegramUnavailable("text-inside-the-exception")
            return super().get_updates(offset, timeout, allowed_updates, limit)

    telegram = Flaky(threading.Event(), [message(1), message(2)])
    _, seen, _ = drive([], telegram=telegram)
    assert len(seen) == 2
    assert "assistant: telegram getUpdates failed: TelegramUnavailable" in caplog.text
    assert "text-inside-the-exception" not in caplog.text
