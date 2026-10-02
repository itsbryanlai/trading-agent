"""Qwen through QwenCloud's OpenAI-compatible API (ADR 0018; research R5).

The endpoint isn't in the source: it comes from RESEARCH_QWEN_BASE_URL, so the same
code serves a pay-as-you-go key or a Token Plan key, each with its own endpoint.

Standard library only: one POST to /chat/completions, in strict JSON Schema mode
(supported for the Qwen3.7-Plus series, per QwenCloud's structured-output guide)
with thinking off (thinking needs streaming there). The key travels in the
Authorization header and never appears in a URL, a log line or an exception. The
prompt and the answer are never logged.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from trading_agent.research.ports import (
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelReply,
    ModelTruncated,
    ModelUnavailable,
)

SCHEMA_NAME = "research_answer"


class QwenClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        max_output_tokens: int,
        timeout: float,
        base_url: str,
        opener: Callable | None = None,
    ) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_output_tokens
        self._timeout = timeout
        self._open = opener or urlopen
        self._url = f"{base_url.rstrip('/')}/chat/completions"

    def __repr__(self) -> str:
        return f"QwenClient(model={self._model!r}, <key hidden>)"

    __str__ = __repr__

    def complete(self, system: str, user: str, schema: dict) -> ModelReply:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self._max_tokens,
            "enable_thinking": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": schema},
            },
        }
        request = Request(
            self._url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            code = exc.code
            if code in (401, 403):
                raise ModelKeyRejected(f"qwen: HTTP {code}") from None
            if code == 429 or code >= 500:
                raise ModelUnavailable(f"qwen: HTTP {code}") from None
            raise ModelRejected(f"qwen: HTTP {code}") from None
        except (OSError, ValueError, HTTPException) as exc:
            raise ModelUnavailable(f"qwen: {type(exc).__name__}") from None
        try:
            reply = json.loads(raw)
            choice = reply["choices"][0]
            finish = choice.get("finish_reason")
            content = choice["message"].get("content")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelUnavailable("qwen: unexpected response shape") from None
        if finish == "length":
            raise ModelTruncated("qwen: answer hit the output limit")
        if finish == "content_filter":
            raise ModelRefused("qwen: content filter")
        if not isinstance(content, str):
            raise ModelUnavailable("qwen: no answer text")
        usage = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
        return ModelReply(
            text=content,
            input_tokens=_count(usage.get("prompt_tokens")),
            output_tokens=_count(usage.get("completion_tokens")),
            finish=finish if isinstance(finish, str) else None,
        )


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
