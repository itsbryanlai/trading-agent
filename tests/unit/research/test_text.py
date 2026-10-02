"""Outside text is cleaned of what Postgres refuses (adversarial review H1)."""

from __future__ import annotations

import pytest

from trading_agent.research.text import clean, has_unsafe


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("plain", "plain"),
        ("a\x00b", "ab"),
        ("bell\x07 and esc\x1b", "bell and esc"),
        ("del\x7f", "del"),
        ("lone \ud800 surrogate", "lone  surrogate"),
        ("keep\nnewline\tand tab", "keep\nnewline\tand tab"),
        ("emoji 📈 and accents é", "emoji 📈 and accents é"),
    ],
)
def test_clean(raw, cleaned):
    assert clean(raw) == cleaned
    assert not has_unsafe(clean(raw))


def test_has_unsafe():
    assert has_unsafe("x\x00") and has_unsafe("\udfff") and not has_unsafe("ok\n")
