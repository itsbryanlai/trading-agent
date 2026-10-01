"""Claude through the official `anthropic` SDK (constitution v1.1.0; ADR 0018; research R5).

The only module that imports `anthropic`. The key and the base URL are passed in
explicitly, so the SDK never takes either from its own environment variables. One
retry at most, so two attempts fit the run budget (research R11). No server-side
refusal fallback: the owner kept it off, so a refusal is a recorded failure rather
than a silent switch to another model (ADR 0018, "no automatic failover"). The
prompt and the answer are never logged.
"""

from __future__ import annotations

import anthropic

from trading_agent.research.config import ModelConfig
from trading_agent.research.ports import (
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelReply,
    ModelTruncated,
    ModelUnavailable,
)

BASE_URL = "https://api.anthropic.com"
MAX_RETRIES = 1


class AnthropicClient:
    def __init__(self, client, model: ModelConfig) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_key(cls, key: str, model: ModelConfig) -> AnthropicClient:
        client = anthropic.Anthropic(
            api_key=key,
            base_url=BASE_URL,
            timeout=model.timeout_seconds,
            max_retries=MAX_RETRIES,
        )
        return cls(client, model)

    def __repr__(self) -> str:
        return f"AnthropicClient(model={self._model.name!r}, <key hidden>)"

    __str__ = __repr__

    def complete(self, system: str, user: str, schema: dict) -> ModelReply:
        try:
            response = self._client.messages.create(
                model=self._model.name,
                max_tokens=self._model.max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "format": {"type": "json_schema", "schema": schema},
                    "effort": self._model.anthropic_effort,
                },
            )
        # Most specific first (the SDK's exception hierarchy).
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ModelKeyRejected(f"anthropic: {type(exc).__name__}") from None
        except anthropic.RateLimitError as exc:
            raise ModelUnavailable(f"anthropic: {type(exc).__name__}") from None
        except anthropic.APIStatusError as exc:
            if 400 <= exc.status_code < 500:
                raise ModelRejected(f"anthropic: HTTP {exc.status_code}") from None
            raise ModelUnavailable(f"anthropic: HTTP {exc.status_code}") from None
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise ModelUnavailable(f"anthropic: {type(exc).__name__}") from None

        stop = response.stop_reason
        if stop == "refusal":
            raise ModelRefused("anthropic: the model declined")
        if stop == "max_tokens":
            raise ModelTruncated("anthropic: answer hit the output limit")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ModelUnavailable("anthropic: no answer text")
        usage = response.usage
        return ModelReply(
            text=text,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            finish=stop,
        )
