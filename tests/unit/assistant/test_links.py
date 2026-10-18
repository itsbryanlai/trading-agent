"""The link filter (specs/013 T005, research A12)."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.assistant import replies
from trading_agent.assistant.links import REMOVED, filter_links

SRC = "https://source.example/a"
WORDS = ("pause", "resume", "buy", "sell", "close")


def test_allowed_url_in_a_sentence_and_before_punctuation():
    for text in (
        f"See {SRC} for more.",
        f"See {SRC}.",
        f"(see {SRC})",
        f"(see {SRC}).",
        f"Read {SRC}, then {SRC}!",
        SRC,
    ):
        assert filter_links(text, [SRC]) == (text, 0)


@pytest.mark.parametrize(
    "text",
    [
        "BRK.B closed at 3.5% on the U.S. exchange, e.g. NVDA in Asia/Singapore.",
        "Price 1.5bn and v1.2.3 at 10.25.",
        "Mon 2026-10-19 23:00 +08 (Mon 11:00 ET)",
        "a@b and @ alone, 3.5%",
    ],
)
def test_ordinary_text_is_kept(text):
    assert filter_links(text, []) == (text, 0)


@pytest.mark.parametrize(
    "bad",
    [
        "evil.com",
        "https://evil.example/x",
        "www.evil.io",
        "t.me/x",
        "@phish_bot",
        "@abcd",
        "@gif",
        "deployment.md",
        "пример.рф",
        "x.xn--p1ai",
        "EVIL.COM/path?q=1",
        "ftp://evil.example",
        "x.evil.com",
        "evil。com",
        "evil．com",
        "evil｡com",
    ],
)
def test_other_links_are_removed(bad):
    out, n = filter_links(f"go to {bad} now", [])
    assert out == f"go to {REMOVED} now"
    assert n == 1


def test_lookalike_of_an_allowed_url_is_removed():
    out, n = filter_links("see https://source.example/b", [SRC])
    assert out == f"see {REMOVED}" and n == 1


@pytest.mark.parametrize("tail", [".evil.com", "@evil.com", "/x", "-x"])
def test_allowed_url_followed_by_more_is_removed_whole(tail):
    out, n = filter_links(f"see {SRC}{tail} ok", [SRC])
    assert out == f"see {REMOVED} ok" and n == 1
    assert "source.example" not in out


def test_counts_each_removal_and_keeps_allowed():
    out, n = filter_links(f"{SRC} and evil.com and @phish_bot", [SRC])
    assert out == f"{SRC} and {REMOVED} and {REMOVED}" and n == 2


def test_forged_placeholder_is_not_restored():
    assert filter_links("0", [SRC]) == ("0", 0)


def test_trailing_sentence_punctuation_survives_a_removal():
    assert filter_links("go to evil.com.", []) == (f"go to {REMOVED}.", 1)


HOST = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}")
ALPHABET = list("abcAB19._-/:@ \n") + ["http", "www", "xn--", "com", "me", "é", "рф"]


@given(st.lists(st.sampled_from(ALPHABET), max_size=40).map("".join))
def test_property_no_host_survives_except_allowed(text):
    out, _ = filter_links(text + " " + SRC, [SRC])
    assert not HOST.search(out.replace(SRC, ""))


def test_every_fixed_reply_passes_unchanged():
    samples = [
        replies.help_text(WORDS, "Asia/Singapore"),
        replies.text_only(),
        replies.refused_line(),
        replies.refused_fallback("sell", WORDS, replies.CONTROLS),
        replies.refused_flag_fallback(WORDS, replies.CONTROLS),
        replies.skipped(1),
        replies.skipped(7),
        replies.too_much(),
        replies.database(),
        *(replies.model_failed(c) for c in replies.MODEL_FAILURES),
    ]
    for text in samples:
        assert filter_links(text, []) == (text, 0)


def test_host_after_dots_is_removed():
    assert filter_links("see ...evil.com now", []) == (f"see ...{REMOVED} now", 1)
