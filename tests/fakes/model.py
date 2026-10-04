"""A stand-in model client for Research's tests: no network, recorded calls."""

from __future__ import annotations

import json

from trading_agent.llm.ports import ModelReply


class FakeModel:
    def __init__(
        self,
        answer: dict | list | str | None = None,
        *,
        error: Exception | None = None,
        input_tokens: int | None = 1200,
        output_tokens: int | None = 300,
    ) -> None:
        if answer is None:
            answer = {"proposals": []}
        self.text = answer if isinstance(answer, str) else json.dumps(answer)
        self.error = error
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, schema: dict) -> ModelReply:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if self.error is not None:
            raise self.error
        return ModelReply(self.text, self.input_tokens, self.output_tokens, "stop")
