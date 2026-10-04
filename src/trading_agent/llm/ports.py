"""The model port shared by the LLM agents (specs/008-portfolio-manager/contracts/ports.md).

Moved verbatim out of research/ports.py (feature 008, research P5). One model call,
nothing else: no writes, no broker. Tests replace it with tests/fakes/model.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ModelError(Exception):
    """`status` is the provider's HTTP status, when there was one. It's logged on its
    own; the message never is, so nothing from a provider's response reaches a log."""

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ModelKeyRejected(ModelError):
    """401 or 403."""


class ModelRejected(ModelError):
    """Any other 4xx: a refused schema, a prompt too long, an unknown model name."""


class ModelUnavailable(ModelError):
    """Network, timeout, 429 or 5xx."""


class ModelRefused(ModelError):
    """The model declined to answer."""


class ModelTruncated(ModelError):
    """The answer hit the output limit, so it can't be trusted to be complete."""


@dataclass(frozen=True)
class ModelReply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    finish: str | None


class ModelClient(Protocol):
    def complete(self, system: str, user: str, schema: dict) -> ModelReply: ...
