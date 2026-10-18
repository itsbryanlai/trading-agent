"""Fixed replies (specs/013 T006, contracts/replies.md, research A14)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.assistant import replies

WORDS = ("pause", "resume", "buy", "sell")


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_controls_are_exactly_the_two_strings():
    assert replies.CONTROLS == (
        "the dashboard's pause control (not built yet)",
        "the deployment runbook, section 5 Switching trading on and "
        "section 6 Switching trading off",
    )


def test_help_names_words_zone_and_limits():
    text = replies.help_text(WORDS, "Asia/Singapore")
    assert "I can't take any action" in text
    assert "pause, resume, buy, sell" in text
    assert "Asia/Singapore" in text


def test_text_only_and_refused_line():
    assert "new text messages" in replies.text_only()
    assert replies.refused_line() == "Nothing was done: I can't take actions."


def test_refused_fallback_starts_with_the_fixed_line_and_names_the_word():
    text = replies.refused_fallback("sell", WORDS, replies.CONTROLS)
    assert text.startswith(replies.refused_line())
    assert "Messages starting with sell are refused" in text
    assert "pause, resume, buy, sell" in text
    assert "dashboard's pause control" in text and "runbook" in text


def test_refused_flag_fallback():
    text = replies.refused_flag_fallback(WORDS, replies.CONTROLS)
    assert text.startswith(replies.refused_line())
    assert "reads as a request to act" in text
    assert "always refused" in text


def test_skipped_pluralises():
    assert "skipped 1 message you" in replies.skipped(1)
    assert "skipped 3 messages you" in replies.skipped(3)


def test_too_much_and_database():
    assert "narrow it" in replies.too_much()
    assert "can't read the data" in replies.database()


@pytest.mark.parametrize("category", replies.MODEL_FAILURES)
def test_model_failed_each_category(category):
    assert f"the model {category}." in replies.model_failed(category)


def test_model_failed_unknown_category():
    with pytest.raises(ValueError):
        replies.model_failed("exploded")


def test_cap():
    assert replies.cap("abc", 3) == "abc"
    assert replies.cap("abcd", 3) == "abc…(cut)"


def test_split_short_text_is_one_part():
    assert replies.split("hello") == ["hello"]


def test_split_prefers_newline_then_space_then_hard():
    assert replies.split("aaa bbb\nccccccc", 9) == ["aaa bbb", "ccccccc"]
    assert replies.split("aaaa bbbb", 6) == ["aaaa", "bbbb"]
    assert replies.split("abcdefghij", 4) == ["abcd", "efgh", "ij"]


def test_split_does_not_cut_a_surrogate_pair():
    parts = replies.split("😀" * 5, 3)
    assert parts == ["😀", "😀", "😀", "😀", "😀"]


text_strategy = st.text(
    alphabet=st.sampled_from(list("ab \n\t") + ["😀", "é", "日"]), max_size=300
)


def test_split_drops_parts_that_are_blank():
    parts = replies.split("a" * 4000 + "\n" + " " * 4001 + "b")
    assert len(parts) == 2 and all(p.strip() for p in parts)


@given(text_strategy, st.integers(min_value=2, max_value=40))
def test_split_property(text, limit):
    parts = replies.split(text, limit)
    assert all(p.strip() for p in parts)
    assert bool(parts) == bool(text.strip())
    assert all(_units(p) <= limit for p in parts)
    assert "".join("".join(parts).split()) == "".join(text.split())


@given(st.text(alphabet=st.sampled_from(["😀", "a", "\n"]), min_size=1, max_size=9000))
def test_split_default_limit_is_4000_units(text):
    assert all(_units(p) <= 4000 for p in replies.split(text))
