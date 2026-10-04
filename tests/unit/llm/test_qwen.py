"""The Qwen adapter, driven by an injected opener: no socket is ever opened
(specs/007-research-agent research R5)."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from trading_agent.llm.ports import (
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
)
from trading_agent.llm.qwen import QwenClient
from trading_agent.research.answer import ANSWER_SCHEMA

BASE_URL = "https://qwen.example.test/compatible-mode/v1"

KEY = "test-dashscope-key-not-real"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Opener:
    def __init__(self, body=None, *, error=None, raw=None):
        self.body, self.error, self.raw = body, error, raw
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return Response(self.raw if self.raw is not None else json.dumps(self.body).encode())


def reply(content='{"proposals": []}', finish="stop", usage=None):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": usage if usage is not None else {"prompt_tokens": 1234, "completion_tokens": 56},
    }


def client(opener, **kw):
    return QwenClient(
        KEY,
        model="qwen3.7-plus",
        max_output_tokens=8000,
        timeout=180,
        opener=opener,
        schema_name="research_answer",
        base_url=kw.pop("base_url", BASE_URL),
        **kw,
    )


def test_request_shape():
    opener = Opener(reply())
    client(opener).complete("SYS json", "USER", ANSWER_SCHEMA)
    request, timeout = opener.requests[0]
    assert request.full_url == f"{BASE_URL}/chat/completions"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert KEY not in request.full_url
    assert timeout == 180
    body = json.loads(request.data)
    assert body["model"] == "qwen3.7-plus"
    assert body["messages"] == [
        {"role": "system", "content": "SYS json"},
        {"role": "user", "content": "USER"},
    ]
    assert body["max_tokens"] == 8000
    assert body["enable_thinking"] is False
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "research_answer", "strict": True, "schema": ANSWER_SCHEMA},
    }


def test_reply_mapping():
    got = client(Opener(reply('{"proposals": [1]}'))).complete("s", "u", {})
    assert (got.text, got.input_tokens, got.output_tokens, got.finish) == (
        '{"proposals": [1]}',
        1234,
        56,
        "stop",
    )


def test_missing_usage_is_unknown_not_zero():
    got = client(Opener(reply(usage={}))).complete("s", "u", {})
    assert (got.input_tokens, got.output_tokens) == (None, None)


def test_truncated_and_filtered_answers():
    with pytest.raises(ModelTruncated):
        client(Opener(reply(finish="length"))).complete("s", "u", {})
    with pytest.raises(ModelRefused):
        client(Opener(reply(finish="content_filter"))).complete("s", "u", {})


def _http(code):
    return HTTPError(f"{BASE_URL}/chat/completions", code, "err", {}, None)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, ModelKeyRejected),
        (403, ModelKeyRejected),
        (400, ModelRejected),
        (404, ModelRejected),
        (422, ModelRejected),
        (429, ModelUnavailable),
        (500, ModelUnavailable),
        (503, ModelUnavailable),
    ],
)
def test_http_errors(code, expected):
    with pytest.raises(expected) as info:
        client(Opener(error=_http(code))).complete("s", "u", {})
    assert KEY not in str(info.value)
    assert info.value.status == code


@pytest.mark.parametrize("error", [URLError("down"), TimeoutError()])
def test_network_errors(error):
    with pytest.raises(ModelUnavailable):
        client(Opener(error=error)).complete("s", "u", {})


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"{}", b'{"choices": []}', b'{"choices": [{"message": {"content": null}}]}'],
)
def test_unreadable_replies(raw):
    with pytest.raises(ModelUnavailable):
        client(Opener(raw=raw)).complete("s", "u", {})


def test_the_key_is_hidden():
    c = client(Opener(reply()))
    assert KEY not in repr(c) and KEY not in str(c)


def test_a_trailing_slash_on_the_base_url_is_harmless():
    opener = Opener(reply())
    client(opener, base_url=BASE_URL + "/").complete("s", "u", {})
    assert opener.requests[0][0].full_url == f"{BASE_URL}/chat/completions"
