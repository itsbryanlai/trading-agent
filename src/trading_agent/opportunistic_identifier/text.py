"""Making outside text safe to store (specs/011 research O8). Pure.

A copy of `research/text.py` (siblings never import each other), after Research's review
H1. The model's rationale and the provider's company name and industry can hold characters
Postgres refuses: a NUL is rejected in `text` and `jsonb`, and a lone UTF-16 surrogate can't
be encoded at all. Either would make the run's single write fail, leaving no row. So every
piece of outside text is cleaned before it can reach a report or the prompt: C0 control
characters (except newline and tab), DEL and lone surrogates are removed.
"""

from __future__ import annotations

import re

_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\ud800-\udfff]")


def clean(text: str) -> str:
    return _UNSAFE.sub("", text)


def has_unsafe(text: str) -> bool:
    return _UNSAFE.search(text) is not None
