"""What happened to one message, and the one log line it gets (contracts/assistant-interface.md).

`Outcome.kind`: answered, too_much, model_failed, database, refused or fixed. Lines carry
counts, names and token counts only: never a message, a reply, a result or an exception's text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from trading_agent.assistant.prompt import PROMPT_VERSION

log = logging.getLogger("trading_agent.assistant")


@dataclass(frozen=True)
class Outcome:
    """`category` is the model-failed code (replies.FAILURE_TEXT), `trigger` the refusal's,
    `word` the matched action word, `detail` an exception type name, a fixed reply's id, or
    a refusal's result (explained or fallback). Token fields are (input, output) or None
    when that call didn't happen."""

    kind: str
    category: str = ""
    trigger: str = ""
    word: str = ""
    detail: str = ""
    queries: tuple[str, ...] = ()
    dropped: tuple[tuple[str, str], ...] = ()
    cut: tuple[str, ...] = ()
    plan_tokens: tuple | None = None
    answer_tokens: tuple | None = None
    refusal_tokens: tuple | None = None
    links_removed: int = 0
    messages: int = 0


@dataclass(frozen=True)
class Handled:
    replies: list[str]
    outcome: Outcome


def done(parts, outcome: Outcome) -> Handled:
    """Count the messages, write the outcome's log line and return both."""
    parts = parts if isinstance(parts, list) else [parts]
    outcome = replace(outcome, messages=outcome.messages or len(parts))
    log.info(line(outcome))
    return Handled(parts, outcome)


def _tokens(pair) -> str:
    if pair is None:
        return "-"
    return "/".join("?" if n is None else str(n) for n in pair)


def line(o: Outcome) -> str:
    if o.kind == "fixed":
        return f"assistant: replied fixed={o.detail}"
    if o.kind == "refused":
        who = f"keyword word={o.word}" if o.trigger == "keyword" else "model_flag"
        calls = [o.plan_tokens] if o.trigger == "model_flag" else []
        tokens = ",".join(_tokens(t) for t in [*calls, o.refusal_tokens])
        return (
            f"assistant: refused prompt_version={PROMPT_VERSION} trigger={who}"
            f" tokens={tokens} outcome={o.detail}"
        )
    label = o.kind + (f":{o.category}" if o.kind == "model_failed" else "")
    return (
        f"assistant: answered prompt_version={PROMPT_VERSION}"
        f" queries={','.join(o.queries) or 'none'}"
        f" dropped={','.join(f'{n}:{r}' for n, r in o.dropped) or 'none'}"
        f" cut={','.join(o.cut) or 'none'}"
        f" tokens={_tokens(o.plan_tokens)},{_tokens(o.answer_tokens)}"
        f" links_removed={o.links_removed} messages={o.messages} outcome={label}"
    )
