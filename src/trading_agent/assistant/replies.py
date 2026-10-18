"""Fixed replies and message limits (contracts/replies.md, research A14).

Plain text, written in code, with no model call. None contains a file name, domain or
`@name`, since the link filter (links.py) would remove it; a test pins this.
"""

from __future__ import annotations

from collections.abc import Sequence

CONTROLS: tuple[str, str] = (
    "the dashboard's pause control (not built yet)",
    "the deployment runbook, section 5 Switching trading on and section 6 Switching trading off",
)

# The log code (no spaces) and the reply's wording for each way a model call fails.
FAILURE_TEXT: dict[str, str] = {
    "unavailable": "was unavailable",
    "refused": "refused",
    "rejected": "rejected the request",
    "truncated": "was cut off",
    "unusable": "gave an unusable answer",
}
MODEL_FAILURES: tuple[str, ...] = tuple(FAILURE_TEXT.values())

CUT_MARK = "…(cut)"
_EXAMPLE = 'phrase it as a question, e.g. "why did the PM sell MU?"'


def _words(action_words: Sequence[str]) -> str:
    return ", ".join(action_words)


def _controls(controls: Sequence[str]) -> str:
    return " or ".join(controls)


def help_text(action_words: Sequence[str], time_zone: str) -> str:
    return (
        "I answer questions about this paper-trading system: positions and cash, the PM's "
        "decisions and the Risk Gate's verdicts, orders and fills, the analysts' reports, "
        "the journal and each agent's book, agent runs, and the system's settings. "
        "I can't take any action. "
        f"Messages starting with these words are refused: {_words(action_words)}. "
        f"To ask about one, {_EXAMPLE}. "
        f"Times are shown in {time_zone} with New York time (ET)."
    )


def text_only() -> str:
    return (
        "I only answer new text messages you write yourself. "
        "Please send your question again as a new message."
    )


def refused_line() -> str:
    return "Nothing was done: I can't take actions."


def refused_fallback(word: str, action_words: Sequence[str], controls: Sequence[str]) -> str:
    return (
        f"{refused_line()}\n"
        f"Messages starting with {word} are refused, as are: {_words(action_words)}. "
        f"To pause or resume trading use {_controls(controls)}. "
        f"To ask about it, {_EXAMPLE}."
    )


def refused_flag_fallback(action_words: Sequence[str], controls: Sequence[str]) -> str:
    return (
        f"{refused_line()}\n"
        "Your message reads as a request to act. "
        f"Messages starting with these words are always refused: {_words(action_words)}. "
        f"To pause or resume trading use {_controls(controls)}. "
        f"To ask about it, {_EXAMPLE}."
    )


def skipped(n: int) -> str:
    noun = "message" if n == 1 else "messages"
    return (
        f"I was offline and skipped {n} {noun} you sent meanwhile. "
        "Please send again anything you still want answered."
    )


def too_much() -> str:
    return (
        "That needs more data than I can read at once. "
        "Please narrow it: one symbol, one agent, or a shorter period."
    )


def database() -> str:
    return "I can't read the data right now. Please try again in a few minutes."


def internal_error() -> str:
    return "Something went wrong answering that. Please try again."


def model_failed(category: str) -> str:
    if category not in MODEL_FAILURES:
        raise ValueError("unknown model failure category")
    return f"I couldn't answer that: the model {category}. Please try again."


def cap(text: str, max_chars: int) -> str:
    """Cut to `max_chars` characters and append the cut mark."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + CUT_MARK


def _units(ch: str) -> int:
    return 2 if ord(ch) > 0xFFFF else 1


def split(text: str, limit_units: int = 4000) -> list[str]:
    """Split into parts of at most `limit_units` UTF-16 code units (Telegram's unit).

    Splits at the last newline or space before the limit (that one character is dropped),
    otherwise hard-splits. A code point is never cut in two. Parts that are blank after
    `strip()` are dropped, so none is empty (Telegram rejects an empty message); the result
    is empty only for blank input.
    """
    return [part for part in _split(text, limit_units) if part.strip()]


def _split(text: str, limit_units: int) -> list[str]:
    if limit_units < 2:
        raise ValueError("limit_units must be at least 2")
    parts: list[str] = []
    rest = text
    while True:
        used = 0
        end = len(rest)
        for i, ch in enumerate(rest):
            used += _units(ch)
            if used > limit_units:
                end = i
                break
        if end == len(rest):
            parts.append(rest)
            return parts
        cut = max(rest.rfind("\n", 0, end + 1), rest.rfind(" ", 0, end + 1))
        if cut > 0:
            parts.append(rest[:cut])
            rest = rest[cut + 1 :]
        else:
            parts.append(rest[:end])
            rest = rest[end:]
