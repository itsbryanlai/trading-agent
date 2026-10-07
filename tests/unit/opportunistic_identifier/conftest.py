"""Fixtures shared by the Opportunistic Identifier's entry-point tests. Every value is an
obvious fake."""

from __future__ import annotations

import pytest

FAKE_URL = "postgresql://oi:fake-not-real@localhost/none"
FAKE_FINNHUB = "fake-finnhub-not-real"
FAKE_DASHSCOPE = "fake-dashscope-not-real"
FAKE_ANTHROPIC = "fake-anthropic-not-real"
FAKE_QWEN_URL = "https://qwen.example.test/compatible-mode/v1"
SECRETS = (FAKE_URL, FAKE_FINNHUB, FAKE_DASHSCOPE, FAKE_ANTHROPIC, "fake-not-real")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("OPPORTUNISTIC_IDENTIFIER_DATABASE_URL", FAKE_URL)
    monkeypatch.setenv("OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY", FAKE_FINNHUB)
    monkeypatch.setenv("OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY", FAKE_DASHSCOPE)
    monkeypatch.setenv("OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL", FAKE_QWEN_URL)
    for other in (
        "OPPORTUNISTIC_IDENTIFIER_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
        "RESEARCH_DASHSCOPE_API_KEY",
        "FINNHUB_API_KEY",
    ):
        monkeypatch.delenv(other, raising=False)
