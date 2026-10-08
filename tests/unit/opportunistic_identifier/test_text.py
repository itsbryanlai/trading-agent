"""Outside text is cleaned of what Postgres refuses (specs/011 research O8; Research's
adversarial review H1)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.opportunistic_identifier import text
from trading_agent.research import text as research_text


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
    assert text.clean(raw) == cleaned
    assert not text.has_unsafe(text.clean(raw))


def test_has_unsafe():
    assert text.has_unsafe("x\x00") and text.has_unsafe("\udfff") and not text.has_unsafe("ok\n")


@given(st.text())
def test_clean_behaves_exactly_like_researchs(raw):
    # The two are copies, since siblings can't import each other.
    assert text.clean(raw) == research_text.clean(raw)
    assert text.has_unsafe(raw) == research_text.has_unsafe(raw)
