"""Fixtures shared by the Portfolio Manager's entry-point tests. Every value is an obvious fake."""

from __future__ import annotations

import pytest

FAKE_URL = "postgresql://portfolio_manager:fake-not-real@localhost/none"
FAKE_FINNHUB = "fake-finnhub-not-real"
FAKE_DASHSCOPE = "fake-dashscope-not-real"
FAKE_ANTHROPIC = "fake-anthropic-not-real"
FAKE_QWEN_URL = "https://qwen.example.test/compatible-mode/v1"
SECRETS = (FAKE_URL, FAKE_FINNHUB, FAKE_DASHSCOPE, FAKE_ANTHROPIC, "fake-not-real")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_MANAGER_DATABASE_URL", FAKE_URL)
    monkeypatch.setenv("PORTFOLIO_MANAGER_FINNHUB_API_KEY", FAKE_FINNHUB)
    monkeypatch.setenv("PORTFOLIO_MANAGER_DASHSCOPE_API_KEY", FAKE_DASHSCOPE)
    monkeypatch.setenv("PORTFOLIO_MANAGER_QWEN_BASE_URL", FAKE_QWEN_URL)
    monkeypatch.delenv("PORTFOLIO_MANAGER_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
