"""The model section of an LLM agent's config, and the names of its provider variables.

Shared by Research and the Portfolio Manager (specs/008-portfolio-manager research
P5; contracts/ports.md). Pure: it reads no environment variable and no file. Each
agent passes its own timeout bounds and its own variable prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

PROVIDERS = ("qwen", "anthropic")
EFFORTS = ("low", "medium", "high")
MAX_OUTPUT_TOKENS_BOUNDS = (1000, 64_000)
_KEYS = {"provider", "name", "anthropic_effort", "max_output_tokens", "timeout_seconds"}


class ModelSettingsError(Exception):
    """A model setting is missing, unknown or out of range. The message names the key."""


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    name: str
    max_output_tokens: int
    timeout_seconds: int
    anthropic_effort: str


def parse_model_settings(section, *, timeout_bounds: tuple[int, int]) -> ModelSettings:
    """Validate a config's `model:` mapping. Strict: every key required, none unknown."""
    if not isinstance(section, dict):
        raise ModelSettingsError("model: must be a mapping")
    for key in sorted(_KEYS - section.keys()):
        raise ModelSettingsError(f"model.{key}: required setting is missing")
    for key in sorted(set(section) - _KEYS, key=str):
        raise ModelSettingsError(f"model.{key}: unknown setting")

    max_output_tokens = _int(
        section["max_output_tokens"], "model.max_output_tokens", *MAX_OUTPUT_TOKENS_BOUNDS
    )
    timeout_seconds = _int(section["timeout_seconds"], "model.timeout_seconds", *timeout_bounds)

    provider = section["provider"]
    if provider not in PROVIDERS:
        raise ModelSettingsError(f"model.provider: must be one of {sorted(PROVIDERS)}")
    name = section["name"]
    if not isinstance(name, str) or not name.strip():
        raise ModelSettingsError("model.name: must be a non-empty string")
    if provider == "anthropic" and not name.startswith("claude-"):
        raise ModelSettingsError("model.name: an anthropic model name starts with 'claude-'")
    effort = section["anthropic_effort"]
    if effort not in EFFORTS:
        raise ModelSettingsError(f"model.anthropic_effort: must be one of {list(EFFORTS)}")
    return ModelSettings(
        provider=provider,
        name=name,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        anthropic_effort=effort,
    )


def provider_variables(prefix: str, provider: str) -> tuple[str, ...]:
    """The environment variables a provider needs, under the agent's prefix.

    The first is the API key. Qwen also needs its endpoint, which depends on the key's type."""
    if provider == "qwen":
        return (f"{prefix}DASHSCOPE_API_KEY", f"{prefix}QWEN_BASE_URL")
    if provider == "anthropic":
        return (f"{prefix}ANTHROPIC_API_KEY",)
    raise ModelSettingsError(f"model.provider: must be one of {sorted(PROVIDERS)}")


def require_https_base_url(value: str, variable: str) -> str:
    """The key is sent to this URL, so it must be https. Named, never echoed."""
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or value != value.strip():
        raise ModelSettingsError(f"{variable} must be an https:// URL")
    return value


def _int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelSettingsError(f"{name}: must be an integer, got {value!r}")
    if not low <= value <= high:
        raise ModelSettingsError(f"{name}: {value} is outside {low}–{high}")
    return value
