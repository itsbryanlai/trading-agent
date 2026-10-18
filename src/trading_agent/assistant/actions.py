"""The action-word match and the refusal document (research A7, A8; ADR 0024, 0025).

Pure. A message is an action request when its first word is on the configured list; the
match decides nothing about data, because no code path writes. The refusal document is the
only thing the model sees on a refusal (contracts/model-io.md).
"""

from __future__ import annotations

import json
import re

TRIGGERS = ("keyword", "model_flag")
_FIRST_WORD = re.compile(r"[^\W_]+")


def match(text: str, action_words) -> str | None:
    """The configured action word the text starts with, or None.

    Leading whitespace and punctuation are skipped; the first run of letters or digits is
    compared case-insensitively, so a number is a first word that is never on the list.
    "Pause!" and "  cancel the order" match; "please pause" and "3 sell orders?" do not.
    """
    found = _FIRST_WORD.search(text)
    if found is None:
        return None
    first = found.group().casefold()
    for word in action_words:
        if first == word.casefold():
            return word
    return None


def refusal_document(text: str, trigger: str, word: str, action_words, controls) -> str:
    """The refusal call's user document: exactly five keys."""
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {list(TRIGGERS)}")
    return json.dumps(
        {
            "message": text,
            "trigger": trigger,
            "matched_word": word if trigger == "keyword" else "",
            "action_words": list(action_words),
            "controls": list(controls),
        },
        ensure_ascii=False,
    )
