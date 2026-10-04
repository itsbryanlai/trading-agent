"""Making the model's text safe to store, and cutting text to a limit (as Research's own
answer module does). Pure.

A NUL is rejected by Postgres `text`, and a lone UTF-16 surrogate can't be encoded at
all; either would make the run's single write fail. C0 control characters (except
newline and tab), DEL and lone surrogates are removed. This is a copy of 30 lines on
purpose: `portfolio_manager` may not import its sibling `research`.
"""

from __future__ import annotations

import re

_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\ud800-\udfff]")


def clean(text: str) -> str:
    return _UNSAFE.sub("", text)


ELLIPSIS = "…"


def cap(value: str, limit: int) -> str:
    """`value` cut to `limit` characters, the last of them an ellipsis when it was cut."""
    if len(value) <= limit:
        return value
    return value[: limit - len(ELLIPSIS)] + ELLIPSIS
