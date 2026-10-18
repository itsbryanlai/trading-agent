"""The question flow (research A9, A12, A14; contracts/model-io.md).

One authorised question in, the reply parts out: plan call, validation, one read-only
transaction, answer call, link filter, cap, split, memory. The model never reaches the store
except through `menu.validate`. The clock, the store and both models are arguments.

Refusals keep to A8: an agent-flagged request returns before any store call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from trading_agent.assistant import replies
from trading_agent.assistant.answer import ANSWER_SCHEMA, PLAN_SCHEMA, parse_answer, parse_plan
from trading_agent.assistant.links import filter_links
from trading_agent.assistant.menu import ENTRIES, Dropped, QueryResult, ValidChoice, validate
from trading_agent.assistant.prompt import (
    ANSWER_SYSTEM,
    PLAN_SYSTEM,
    PROMPT_VERSION,
    answer_document,
    fit,
    plan_document,
)
from trading_agent.assistant.store import StoreError
from trading_agent.llm.ports import (
    ModelError,
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
)

log = logging.getLogger("trading_agent.assistant")

UNUSABLE = "gave an unusable answer"
_NOT_YET = Dropped("settings", "not_available_yet")  # TODO T015: settings come from files


@dataclass(frozen=True)
class Outcome:
    """What happened to one message, for the loop and the log line.

    `kind`: answered, too_much, model_failed, database or refused. `category` is the
    model-failed text, `trigger` the refusal's, `detail` an exception type name.
    `plan_tokens` and `answer_tokens` are (input, output) or None when the call didn't happen.
    """

    kind: str
    category: str = ""
    trigger: str = ""
    detail: str = ""
    queries: tuple[str, ...] = ()
    dropped: tuple[tuple[str, str], ...] = ()
    cut: tuple[str, ...] = ()
    plan_tokens: tuple | None = None
    answer_tokens: tuple | None = None
    links_removed: int = 0
    messages: int = 0


@dataclass(frozen=True)
class Handled:
    replies: list[str]
    outcome: Outcome


def handle(
    message_text: str, *, now: datetime, config, store, plan_model, answer_model, memory
) -> Handled:
    """Answer one question. `now` carries the owner's ZoneInfo."""
    limits = config.limits
    exchanges = memory.recent(now)

    def build_plan(kept):
        return plan_document(
            now, message_text, kept, ENTRIES,
            max_queries=limits.max_queries_per_question,
            text_field_chars=limits.text_field_chars,
        )

    fitted = fit(build_plan, exchanges, limits.max_input_chars)
    if fitted is None:
        return _done(replies.too_much(), Outcome("too_much"))
    try:
        reply = plan_model.complete(PLAN_SYSTEM, fitted[0], PLAN_SCHEMA)
    except ModelError as exc:
        return _failed(_category(exc))
    plan_tokens = (reply.input_tokens, reply.output_tokens)
    plan = parse_plan(reply.text)
    if plan is not None and plan.action_request:
        return _flagged(config, plan_tokens)

    valid: list[ValidChoice] = []
    dropped: list[Dropped] = []
    results: list[QueryResult] = []
    try:
        if plan is not None:
            valid, dropped = validate(
                plan.choices,
                known_symbols=store.known_symbols(),
                today_local=now.date(),
                zone=now.tzinfo,
                max_span_days=limits.max_span_days,
                max_queries=limits.max_queries_per_question,
            )
            runnable = [c for c in valid if c.name != "settings"]  # TODO T015
            dropped += [_NOT_YET] * (len(valid) - len(runnable))
            valid = runnable
            results = store.run(valid) if valid else []
    except StoreError as exc:
        return _done(replies.database(), Outcome("database", detail=type(exc).__name__,
                                                  plan_tokens=plan_tokens))

    def build_answer(kept):
        return answer_document(
            now, message_text, kept, results, dropped, not results,
            text_field_chars=limits.text_field_chars,
        )

    seen = {
        "queries": tuple(r.name for r in results),
        "dropped": tuple((d.name, d.reason) for d in dropped),
        "cut": tuple(r.name for r in results if r.cut),
        "plan_tokens": plan_tokens,
    }
    fitted = fit(build_answer, exchanges, limits.max_input_chars)
    if fitted is None:
        return _done(replies.too_much(), Outcome("too_much", **seen))
    try:
        answered = answer_model.complete(ANSWER_SYSTEM, fitted[0], ANSWER_SCHEMA)
    except ModelError as exc:
        return _failed(_category(exc), **seen)
    seen["answer_tokens"] = (answered.input_tokens, answered.output_tokens)
    text = parse_answer(answered.text)
    if text is None:
        return _failed(UNUSABLE, **seen)

    text, removed = filter_links(text, _source_urls(results))
    text = replies.cap(text, limits.max_reply_chars)
    parts = replies.split(text)
    memory.add(now, message_text, text)
    return _done(parts, Outcome("answered", links_removed=removed, messages=len(parts), **seen))


def _flagged(config, plan_tokens) -> Handled:
    # TODO T019: the refusal call. Until then the fixed fallback, with no store call.
    text = replies.refused_flag_fallback(config.action_words, replies.CONTROLS)
    return _done(
        [text], Outcome("refused", trigger="model_flag", plan_tokens=plan_tokens, messages=1)
    )


def _category(exc: ModelError) -> str:
    if isinstance(exc, ModelRefused):
        return "refused"
    if isinstance(exc, ModelRejected | ModelKeyRejected):
        return "rejected the request"
    if isinstance(exc, ModelTruncated):
        return "was cut off"
    return "was unavailable"


def _failed(category: str, **seen) -> Handled:
    return _done(
        [replies.model_failed(category)], Outcome("model_failed", category=category, **seen)
    )


def _source_urls(results) -> list[str]:
    urls: list[str] = []
    for result in results:
        if result.name != "reports":
            continue
        for row in result.rows:
            sources = row.get("sources") if isinstance(row, dict) else None
            for source in sources if isinstance(sources, list) else []:
                url = source.get("url") if isinstance(source, dict) else None
                if isinstance(url, str) and url:
                    urls.append(url)
    return urls


def _done(parts, outcome: Outcome) -> Handled:
    parts = parts if isinstance(parts, list) else [parts]
    outcome = Outcome(**{**outcome.__dict__, "messages": outcome.messages or len(parts)})
    if outcome.kind != "refused":
        log.info(_line(outcome))
    return Handled(parts, outcome)


def _tokens(pair) -> str:
    if pair is None:
        return "-"
    return "/".join("?" if n is None else str(n) for n in pair)


def _line(o: Outcome) -> str:
    label = o.kind + (f":{o.category}" if o.kind == "model_failed" else "")
    return (
        f"assistant: answered prompt_version={PROMPT_VERSION}"
        f" queries={','.join(o.queries) or 'none'}"
        f" dropped={','.join(f'{n}:{r}' for n, r in o.dropped) or 'none'}"
        f" cut={','.join(o.cut) or 'none'}"
        f" tokens={_tokens(o.plan_tokens)},{_tokens(o.answer_tokens)}"
        f" links_removed={o.links_removed} messages={o.messages} outcome={label}"
    )
