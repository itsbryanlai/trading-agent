"""The model section of an LLM agent's config (specs/008-portfolio-manager research P5)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    name: str
    max_output_tokens: int
    timeout_seconds: int
    anthropic_effort: str
