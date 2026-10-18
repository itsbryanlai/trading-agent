"""The link filter (research A12, FR-013a).

Telegram turns URLs, host names, t.me paths and @usernames into links even in plain text,
so every model reply is filtered before it is sent. The only links kept are the exact
`url` strings of the reports the question returned.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

REMOVED = "[link removed]"

_OPEN, _CLOSE = "", ""
_PUNCT = ".,;:!?)]}\"'"
_PLACEHOLDER = re.compile(f"{_OPEN}(\\d+){_CLOSE}")

_SCHEME_OR_WWW = r"(?:[A-Za-z][A-Za-z0-9+.\-]*://|www\.)\S*"
# label.label...tld: the last label starts with 2+ letters (so BRK.B, 3.5%, U.S. and e.g. do
# not match). Covers IDN hosts and xn-- punycode. An optional path/query follows.
_DOT = "[.\u3002\uff0e\uff61]"
_HOST = r"(?<![\w\-])(?:[\w\-]+" + _DOT + r")+[^\W\d_]{2,}[\w\-]*(?:[/?#]\S*)?"
# Telegram links 4-character usernames and some 3-character inline bots such as @gif.
_USERNAME = r"(?<![\w@])@\w{3,32}(?!\w)"
_IPV4 = r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?!\d)(?::\d+)?(?:/\S*)?"
# A host is tried before a username so "@name.com" leaves no host-like tail behind.
_OTHER = re.compile(f"{_SCHEME_OR_WWW}|{_IPV4}|(?:@?{_HOST})|{_USERNAME}")


def _protect(text: str, allowed: list[str]) -> tuple[str, list[str]]:
    """Swap each allowed URL, where it ends at a boundary, for a placeholder."""
    if not allowed:
        return text, []
    urls = sorted(set(allowed), key=len, reverse=True)
    alternatives = "|".join(re.escape(u) for u in urls)
    boundary = f"(?=[{re.escape(_PUNCT)}]*(?:\\s|$))"
    pattern = re.compile(f"(?<![\\w@./\\-])(?:{alternatives}){boundary}")
    found: list[str] = []

    def swap(match: re.Match[str]) -> str:
        found.append(match.group(0))
        return f"{_OPEN}{len(found) - 1}{_CLOSE}"

    return pattern.sub(swap, text), found


def filter_links(reply: str, allowed_urls: Iterable[str]) -> tuple[str, int]:
    """Return the reply with every link but the allowed URLs replaced, and the count."""
    text = reply.replace(_OPEN, "").replace(_CLOSE, "")
    allowed = [u for u in allowed_urls if u]
    text, protected = _protect(text, allowed)
    removed = 0

    def drop(match: re.Match[str]) -> str:
        nonlocal removed
        run = match.group(0)
        tail = ""
        while run and run[-1] in _PUNCT:
            tail = run[-1] + tail
            run = run[:-1]
        if not run:
            return match.group(0)
        removed += 1
        return REMOVED + tail

    text = _OTHER.sub(drop, text)
    text = _PLACEHOLDER.sub(lambda m: protected[int(m.group(1))], text)
    return text, removed
