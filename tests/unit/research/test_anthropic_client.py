"""The Anthropic adapter with an injected fake client: the real SDK client is never
constructed against the network (specs/007-research-agent research R5; analyze G1, A4)."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from tests.unit.research.support import config
from trading_agent.research import anthropic_client as module
from trading_agent.research.answer import ANSWER_SCHEMA
from trading_agent.research.anthropic_client import AnthropicClient
from trading_agent.research.ports import (
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
)

KEY = "fake-anthropic-not-real"
MODEL = config(model_provider="anthropic", model_name="claude-sonnet-5-5").model


def message(text='{"proposals": []}', stop="end_turn", blocks=None):
    content = blocks if blocks is not None else [SimpleNamespace(type="text", text=text)]
    usage = SimpleNamespace(input_tokens=1500, output_tokens=120)
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage)


class FakeMessages:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply or message(), error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.reply


def client(reply=None, error=None):
    messages = FakeMessages(reply, error)
    return AnthropicClient(SimpleNamespace(messages=messages), MODEL), messages


def test_the_request():
    c, messages = client()
    c.complete("SYS", "USER", ANSWER_SCHEMA)
    (call,) = messages.calls
    assert call == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 8000,
        "system": "SYS",
        "messages": [{"role": "user", "content": "USER"}],
        "output_config": {
            "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
            "effort": "medium",
        },
    }
    assert "fallbacks" not in call and "betas" not in call  # the owner keeps it off


def test_the_reply():
    got = client(message('{"proposals": [1]}'))[0].complete("s", "u", {})
    assert (got.text, got.input_tokens, got.output_tokens, got.finish) == (
        '{"proposals": [1]}',
        1500,
        120,
        "end_turn",
    )


def test_the_first_text_block_is_the_answer():
    blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="T")]
    assert client(message(blocks=blocks))[0].complete("s", "u", {}).text == "T"


def test_stop_reasons():
    with pytest.raises(ModelRefused):
        client(message(stop="refusal"))[0].complete("s", "u", {})
    with pytest.raises(ModelTruncated):
        client(message(stop="max_tokens"))[0].complete("s", "u", {})
    with pytest.raises(ModelUnavailable):
        client(message(blocks=[]))[0].complete("s", "u", {})


_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(cls, code):
    return cls("err", response=httpx2.Response(code, request=_REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_status(anthropic.AuthenticationError, 401), ModelKeyRejected),
        (_status(anthropic.PermissionDeniedError, 403), ModelKeyRejected),
        (_status(anthropic.BadRequestError, 400), ModelRejected),
        (_status(anthropic.NotFoundError, 404), ModelRejected),
        (_status(anthropic.UnprocessableEntityError, 422), ModelRejected),
        (_status(anthropic.APIStatusError, 409), ModelRejected),
        (_status(anthropic.RateLimitError, 429), ModelUnavailable),
        (_status(anthropic.InternalServerError, 500), ModelUnavailable),
        (_status(anthropic.APIStatusError, 529), ModelUnavailable),
        (anthropic.APIConnectionError(request=_REQUEST), ModelUnavailable),
        (anthropic.APITimeoutError(request=_REQUEST), ModelUnavailable),
    ],
)
def test_errors(error, expected):
    with pytest.raises(expected) as info:
        client(error=error)[0].complete("s", "u", {})
    status = getattr(error, "status_code", None)
    assert info.value.status == status


def test_construction_passes_the_key_base_url_timeout_and_one_retry(monkeypatch):
    seen = {}

    def recorder(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(messages=FakeMessages())

    monkeypatch.setattr(module.anthropic, "Anthropic", recorder)
    c = AnthropicClient.from_key(KEY, MODEL)
    assert seen == {
        "api_key": KEY,
        "base_url": "https://api.anthropic.com",
        "timeout": 180,
        "max_retries": 1,
    }
    assert KEY not in repr(c) and KEY not in str(c)
