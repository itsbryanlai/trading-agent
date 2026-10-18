"""The Telegram client (specs/013 tasks T003; research A1, A2). No network: an injected opener."""

from __future__ import annotations

import io
import json
from http.client import InvalidURL
from urllib.error import HTTPError, URLError

import pytest

from tests.fakes.telegram import FakeTelegram
from trading_agent.assistant.telegram import (
    Conflict,
    FloodControl,
    TelegramClient,
    TelegramUnavailable,
    TokenRejected,
    is_valid_token,
)

TOKEN = "000000:fake-not-real"


class Response:
    def __init__(self, payload):
        self._raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []
        self.timeouts = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return Response(self.outcome)


def _client(outcome, timeout=60):
    opener = Opener(outcome)
    return TelegramClient(TOKEN, opener=opener, timeout=timeout), opener


def _http(code, body=b"{}"):
    return HTTPError(f"https://api.telegram.org/bot{TOKEN}/x", code, "msg", {}, io.BytesIO(body))


def test_get_updates_posts_json_to_the_method_url():
    client, opener = _client({"ok": True, "result": [{"update_id": 5}]})
    result = client.get_updates(7, 50, ["message", "edited_message"], 100)
    assert result == [{"update_id": 5}]
    request = opener.requests[0]
    assert request.full_url == f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {
        "offset": 7,
        "timeout": 50,
        "allowed_updates": ["message", "edited_message"],
        "limit": 100,
    }
    assert opener.timeouts == [60]


def test_send_message_body_has_exactly_the_allowed_keys():
    client, opener = _client({"ok": True, "result": {"message_id": 1}})
    client.send_message(42, "hello")
    body = json.loads(opener.requests[0].data)
    assert set(body) == {"chat_id", "text", "link_preview_options"}
    assert body["link_preview_options"] == {"is_disabled": True}
    assert body["chat_id"] == 42
    assert opener.requests[0].full_url.endswith("/sendMessage")


def test_get_webhook_info_returns_the_result_from_its_method():
    client, opener = _client({"ok": True, "result": {"url": ""}})
    assert client.get_webhook_info() == {"url": ""}
    assert opener.requests[0].full_url.endswith("/getWebhookInfo")


def test_get_webhook_info_with_a_wrong_shape_is_unavailable():
    client, _ = _client({"ok": True, "result": []})
    with pytest.raises(TelegramUnavailable):
        client.get_webhook_info()


def test_get_me_returns_the_result():
    client, _ = _client({"ok": True, "result": {"id": 1, "username": "b"}})
    assert client.get_me() == {"id": 1, "username": "b"}


@pytest.mark.parametrize("code", [401, 404])
def test_401_and_404_mean_the_token_is_rejected(code):
    client, _ = _client(_http(code))
    with pytest.raises(TokenRejected):
        client.get_me()


def test_409_is_a_conflict():
    client, _ = _client(_http(409))
    with pytest.raises(Conflict):
        client.get_updates(0, 0, [], 1)


def test_429_carries_retry_after():
    body = json.dumps({"ok": False, "error_code": 429, "parameters": {"retry_after": 17}})
    client, _ = _client(_http(429, body.encode()))
    with pytest.raises(FloodControl) as caught:
        client.send_message(1, "x")
    assert caught.value.retry_after == 17


def test_429_without_a_usable_retry_after_waits_one_second():
    client, _ = _client(_http(429, b"not json"))
    with pytest.raises(FloodControl) as caught:
        client.send_message(1, "x")
    assert caught.value.retry_after == 1


@pytest.mark.parametrize(
    "outcome",
    [
        _http(500),
        _http(502),
        _http(400),
        URLError("boom"),
        TimeoutError("slow"),
        OSError("down"),
        InvalidURL("bad"),
        ValueError("bad"),
        {"ok": False, "description": "nope"},
        {"ok": True},
        b"<html>",
        b"[]",
    ],
)
def test_other_failures_are_unavailable(outcome):
    client, _ = _client(outcome)
    with pytest.raises(TelegramUnavailable):
        client.get_me()


def test_a_wrong_result_shape_is_unavailable():
    client, _ = _client({"ok": True, "result": "text"})
    with pytest.raises(TelegramUnavailable):
        client.get_updates(0, 0, [], 1)
    client, _ = _client({"ok": True, "result": []})
    with pytest.raises(TelegramUnavailable):
        client.get_me()


def test_ok_false_with_an_error_code_is_classified():
    client, _ = _client({"ok": False, "error_code": 409, "description": "x"})
    with pytest.raises(Conflict):
        client.get_me()


def test_no_exception_or_client_text_contains_the_token():
    outcomes = [
        _http(401),
        _http(409),
        _http(429),
        _http(500),
        URLError(TOKEN),
        InvalidURL(TOKEN),
        ValueError(TOKEN),
        {"ok": False, "description": TOKEN},
    ]
    for outcome in outcomes:
        client, _ = _client(outcome)
        with pytest.raises(Exception) as caught:
            client.get_me()
        assert TOKEN not in str(caught.value)
        assert TOKEN not in repr(caught.value)
        assert TOKEN not in str(client)
        assert TOKEN not in repr(client)
    assert "fake-not-real" not in repr(TelegramClient(TOKEN, timeout=1))


@pytest.mark.parametrize(
    "outcome",
    [
        _http(401),
        _http(409),
        _http(429),
        _http(500),
        URLError(TOKEN),
        InvalidURL(TOKEN),
        ValueError(TOKEN),
        OSError(TOKEN),
        TimeoutError(TOKEN),
    ],
)
def test_a_raised_error_carries_no_exception_context(outcome):
    import traceback

    client, _ = _client(outcome)
    with pytest.raises(Exception) as caught:
        client.get_me()
    error = caught.value
    assert error.__context__ is None and error.__cause__ is None
    assert TOKEN not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize("token", ["123456:ABC-def_1", "000000:fake-not-real"])
def test_valid_tokens(token):
    assert is_valid_token(token)


@pytest.mark.parametrize("token", ["", "abc", "123:", ":abc", "12 3:abc", "123:ab/c", "123:a\nb"])
def test_invalid_tokens(token):
    assert not is_valid_token(token)


def test_the_fake_confirms_updates_below_the_offset_and_records_sends():
    fake = FakeTelegram([{"update_id": 1}, {"update_id": 2}, {"update_id": 3}])
    assert [u["update_id"] for u in fake.get_updates(2, 0, [], 1)] == [2]
    assert [u["update_id"] for u in fake.get_updates(3, 0, [], 100)] == [3]
    assert fake.offsets == [2, 3]
    fake.send_message(9, "hi")
    assert fake.sent == [(9, "hi")]
    assert fake.get_me()["is_bot"] is True
