"""The action-word match and the refusal document (specs/013 tasks T004; research A7, A8)."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.assistant.actions import match, refusal_document

WORDS = ["pause", "resume", "unpause", "halt", "buy", "sell", "cancel", "trade", "liquidate"]


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("Pause!", "pause"),
        ("SELL AAPL", "sell"),
        ("  cancel the order", "cancel"),
        ("...halt", "halt"),
        ("Liquidate everything", "liquidate"),
        ("'buy'", "buy"),
    ],
)
def test_action_words_at_the_start_match(text, word):
    assert match(text, WORDS) == word


@pytest.mark.parametrize(
    "text",
    [
        "Why did the PM sell MU?",
        "is trading paused?",
        "Stop-loss triggers this week?",
        "close price of MU?",
        "please pause trading",
        "stop trading",
        "",
        "   ",
        "!!!",
        "pauses",
        "selling",
        "Hello",
        "3 sell orders?",
        "10 buy",
        "2nd sell",
    ],
)
def test_other_text_does_not_match(text):
    assert match(text, WORDS) is None


def test_a_word_not_in_the_list_does_not_match():
    assert match("pause", ["resume"]) is None


@given(st.text())
def test_a_first_letter_run_off_the_list_never_matches(text):
    import re

    found = re.search(r"[^\W_]+", text)
    if found is None or found.group().casefold() not in WORDS:
        assert match(text, WORDS) is None


@given(st.sampled_from(WORDS), st.text(alphabet=" .,!?-'\"", max_size=5), st.text(max_size=20))
def test_a_listed_word_after_punctuation_always_matches(word, lead, tail):
    text = f"{lead}{word.upper()} {tail}"
    assert match(text, WORDS) == word


def test_the_refusal_document_has_exactly_five_keys():
    controls = ["the dashboard's pause control (not built yet)", "the runbook"]
    doc = json.loads(refusal_document("Pause!", "keyword", "pause", WORDS, controls))
    assert list(doc) == ["message", "trigger", "matched_word", "action_words", "controls"]
    assert doc == {
        "message": "Pause!",
        "trigger": "keyword",
        "matched_word": "pause",
        "action_words": WORDS,
        "controls": controls,
    }


def test_a_model_flag_refusal_has_an_empty_matched_word():
    doc = json.loads(refusal_document("please pause", "model_flag", "pause", WORDS, ["c"]))
    assert doc["trigger"] == "model_flag"
    assert doc["matched_word"] == ""


def test_an_unknown_trigger_is_refused():
    with pytest.raises(ValueError):
        refusal_document("x", "other", "", WORDS, [])
