"""Checking the model's answer (specs/007-research-agent research R6; spec FR-007–FR-010).

Pure. The model reads text anyone can publish, so nothing it says is trusted:
every proposal passes every rule below, in this order, or is dropped with exactly
one reason. Citations in a written report are rebuilt from the fetched articles,
never from the model's text (FR-008).

SC-002 ("nothing invalid is ever written") is a Hypothesis property of `check`
(tests/unit/research/test_answer_property.py).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from trading_agent.reference.symbols import is_plausible_ticker
from trading_agent.research import text
from trading_agent.research.selection import Article

# The drop reasons, in the order the rules apply (a closed set; contracts).
MALFORMED_ANSWER = "malformed_answer"
INVALID_SYMBOL = "invalid_symbol"
UNLISTED_SYMBOL = "unlisted_symbol"
INVALID_DIRECTION = "invalid_direction"
INVALID_CONVICTION = "invalid_conviction"
INVALID_SIZE = "invalid_size"
NO_CITATION = "no_citation"
UNKNOWN_CITATION = "unknown_citation"
UNCITED_SYMBOL = "uncited_symbol"
DUPLICATE_SYMBOL = "duplicate_symbol"
ALREADY_OPEN = "already_open"
DROP_REASONS = (
    MALFORMED_ANSWER,
    INVALID_SYMBOL,
    UNLISTED_SYMBOL,
    INVALID_DIRECTION,
    INVALID_CONVICTION,
    INVALID_SIZE,
    NO_CITATION,
    UNKNOWN_CITATION,
    UNCITED_SYMBOL,
    DUPLICATE_SYMBOL,
    ALREADY_OPEN,
)

DIRECTIONS = ("buy", "sell")
ELLIPSIS = "…"
_THOUSANDTH = Decimal("0.001")
_HUNDRED = Decimal(100)

# One table, from which the schema sent to the provider is generated, so the prompt's
# schema and this checker can't drift apart. Only keywords both providers accept in
# strict mode (types, enum, required, additionalProperties); ranges are checked here.
_FIELDS: dict[str, dict] = {
    "symbol": {"type": "string"},
    "direction": {"type": "string", "enum": list(DIRECTIONS)},
    "conviction": {"type": "integer"},
    "suggested_size_pct": {"type": "number"},
    "rationale": {"type": "string"},
    "article_ids": {"type": "array", "items": {"type": "string"}},
}
ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _FIELDS,
                "required": list(_FIELDS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["proposals"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CheckedReport:
    symbol: str
    direction: str
    conviction: int
    size: Decimal
    rationale: str
    sources: tuple[dict, ...]


@dataclass(frozen=True)
class Drop:
    index: int
    symbol: str | None
    reason: str


@dataclass(frozen=True)
class Checked:
    reports: tuple[CheckedReport, ...]
    drops: tuple[Drop, ...]
    unusable: bool  # the answer as a whole isn't {"proposals": [...]}
    received: int  # proposals in the answer


def check(
    text: str,
    articles: Mapping[str, Article],
    listings: Mapping[str, str],
    open_reports: Collection[tuple[str, str]],
    rationale_max_chars: int,
) -> Checked:
    try:
        answer = json.loads(text)
    except (TypeError, ValueError):
        return Checked((), (), unusable=True, received=0)
    if (
        not isinstance(answer, dict)
        or set(answer) != {"proposals"}
        or not isinstance(answer["proposals"], list)
    ):
        return Checked((), (), unusable=True, received=0)

    proposals = answer["proposals"]
    open_set = set(open_reports)
    accepted: list[CheckedReport] = []
    drops: list[Drop] = []
    for index, item in enumerate(proposals):
        result = _check_one(item, articles, listings, rationale_max_chars)
        if isinstance(result, Drop):
            drops.append(Drop(index, result.symbol, result.reason))
        elif any(r.symbol == result.symbol for r in accepted):
            drops.append(Drop(index, result.symbol, DUPLICATE_SYMBOL))
        elif (result.symbol, result.direction) in open_set:
            drops.append(Drop(index, result.symbol, ALREADY_OPEN))
        else:
            accepted.append(result)
    return Checked(tuple(accepted), tuple(drops), unusable=False, received=len(proposals))


def _check_one(item, articles, listings, rationale_max_chars) -> CheckedReport | Drop:
    if not isinstance(item, dict) or set(item) != set(_FIELDS):
        return Drop(0, None, MALFORMED_ANSWER)
    rationale = item["rationale"]
    if isinstance(rationale, str):
        rationale = text.clean(rationale)  # review H1: no NUL or lone surrogate is stored
    if not isinstance(rationale, str) or not rationale.strip():
        return Drop(0, None, MALFORMED_ANSWER)

    symbol = item["symbol"]
    if not is_plausible_ticker(symbol):
        return Drop(0, None, INVALID_SYMBOL)
    if symbol not in listings:
        return Drop(0, symbol, UNLISTED_SYMBOL)

    direction = item["direction"]
    if direction not in DIRECTIONS:
        return Drop(0, symbol, INVALID_DIRECTION)

    conviction = item["conviction"]
    if isinstance(conviction, bool) or not isinstance(conviction, int) or not 1 <= conviction <= 5:
        return Drop(0, symbol, INVALID_CONVICTION)

    size = _size(item["suggested_size_pct"], direction)
    if size is None:
        return Drop(0, symbol, INVALID_SIZE)

    ids = item["article_ids"]
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        return Drop(0, symbol, NO_CITATION)
    if any(i not in articles for i in ids):
        return Drop(0, symbol, UNKNOWN_CITATION)
    cited = [articles[i] for i in dict.fromkeys(ids)]
    # Only citations about the company are kept as sources, each marked primary or
    # secondary; at least one is needed (spec Clarifications 2026-10-03).
    relevant = [(a, relevance(a, symbol, listings[symbol])) for a in cited]
    relevant = [(a, level) for a, level in relevant if level is not None]
    if not relevant:
        return Drop(0, symbol, UNCITED_SYMBOL)

    return CheckedReport(
        symbol=symbol,
        direction=direction,
        conviction=conviction,
        size=size,
        rationale=_cap(rationale.strip(), rationale_max_chars),
        sources=tuple(
            {
                "title": a.title,
                "url": a.url,
                "publisher": a.publisher,
                "published_at": a.published_at.isoformat(),
                "relevance": level,
            }
            for a, level in relevant
        ),
    )


# --- relevance (spec Clarifications 2026-10-02, option 3) ---------------------------

# Corporate words dropped from the end (and "THE" from the start) of a listing's name,
# so "AMERICAN AIRLINES GROUP INC" is matched as "AMERICAN AIRLINES".
_SUFFIXES = frozenset(
    """INC INCORPORATED CORP CORPORATION CO COMPANY LTD LIMITED PLC LLC LP SA NV AG SE
    HOLDINGS HOLDING GROUP CL CLASS A B C SHS ORD ADR DE NEW COM""".split()
)
MIN_NAME_CHARS = 4  # shorter names ("AT T", "GE") match too much ordinary text
# Listing names that are everyday words (review M2): "price target", "the news", "a
# block" say nothing about the company, so their name never makes a source primary.
# Not exhaustive; extend it when a dry run shows another.
COMMON_WORD_NAMES = frozenset(
    """TARGET NEWS SNAP BLOCK PROGRESSIVE BALL CARRIER ROOT UNITY GLOBAL GENERAL
    NATIONAL UNITED AMERICAN FIRST CATALYST FRONTIER""".split()
)
# A ticker this short in parentheses reads as an abbreviation, e.g. "(AI)" (review M2).
MIN_PAREN_TICKER_CHARS = 3
_EXCHANGES = r"(?:NASDAQ|NYSE(?:\s+AMERICAN)?|AMEX|CBOE)"


def company_name(listing_name: str) -> str | None:
    """The words to look for in an article, or None when too short to be safe."""
    words = re.sub(r"[^A-Z0-9]+", " ", listing_name.upper()).split()
    if words[:1] == ["THE"]:
        words = words[1:]
    while words and words[-1] in _SUFFIXES:
        words.pop()
    name = " ".join(words)
    return name if len(name.replace(" ", "")) >= MIN_NAME_CHARS else None


PRIMARY = "primary"
SECONDARY = "secondary"


def relevance(article: Article, symbol: str, listing_name: str) -> str | None:
    """How this article relates to `symbol` (spec Clarifications 2026-10-03):
    - primary: its headline or summary names the company, or gives the ticker as
      $SYM, (SYM) or EXCHANGE: SYM;
    - secondary: only tagged with the symbol, or from its company-news feed, which
      also carries loosely related articles;
    - None: neither, so it can't be cited for that symbol."""
    text = f"{article.title} {article.summary}"
    if _names(text, symbol, listing_name):
        return PRIMARY
    if symbol in article.related:
        return SECONDARY
    return None


def cites(article: Article, symbol: str, listing_name: str) -> bool:
    """Can this article be cited for `symbol`? Primary or secondary both count; the
    PM sees which on each source."""
    return relevance(article, symbol, listing_name) is not None


def _names(text: str, symbol: str, listing_name: str) -> bool:
    ticker = re.escape(symbol)
    forms = [
        rf"\${ticker}\b",  # $AAPL
        rf"\(\s*{_EXCHANGES}\s*:\s*{ticker}\s*\)",  # (NASDAQ: AAPL)
        rf"\b{_EXCHANGES}\s*:\s*{ticker}\b",  # NASDAQ: AAPL
    ]
    if len(symbol) >= MIN_PAREN_TICKER_CHARS:
        forms.append(rf"\(\s*{ticker}\s*\)")  # (AAPL)
    if any(re.search(form, text) for form in forms):
        return True
    name = company_name(listing_name)
    if name is None or name in COMMON_WORD_NAMES:
        return False
    words = " ".join(re.sub(r"[^A-Z0-9]+", " ", text.upper()).split())
    return f" {name} " in f" {words} "


def _size(value, direction: str) -> Decimal | None:
    """A target weight, rounded down to 3 places. Buy: above 0; sell: 0 or more."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        exact = Decimal(str(value))
    except InvalidOperation:
        return None
    if exact > _HUNDRED or exact < 0:
        return None
    size = exact.quantize(_THOUSANDTH, rounding=ROUND_DOWN)
    if direction == "buy" and size <= 0:
        return None
    return size


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(ELLIPSIS)] + ELLIPSIS
