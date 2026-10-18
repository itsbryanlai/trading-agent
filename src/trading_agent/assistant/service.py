"""The question flow (research A9, A12, A14; contracts/model-io.md).

One authorised question in, the reply parts out: plan call, validation, one read-only
transaction, answer call, link filter, cap, split, memory. The model never reaches the store
except through `menu.validate`. The clock, the store and both models are arguments.

Refusals keep to A8: an agent-flagged request returns before any store call.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from trading_agent.assistant import replies, settings_view
from trading_agent.assistant.actions_flow import refuse
from trading_agent.assistant.answer import ANSWER_SCHEMA, PLAN_SCHEMA, parse_answer, parse_plan
from trading_agent.assistant.config import DEFAULT_CONFIG_PATH
from trading_agent.assistant.inbox import ActionRequest, Command, TextOnly, classify
from trading_agent.assistant.links import filter_links
from trading_agent.assistant.menu import (
    ENTRIES,
    Dropped,
    QueryResult,
    ValidChoice,
    symbols_to_check,
    validate,
)
from trading_agent.assistant.outcome import Handled, Outcome, done
from trading_agent.assistant.prompt import (
    ANSWER_SYSTEM,
    PLAN_SYSTEM,
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

UNUSABLE = "unusable"


def respond(update: dict, *, now: datetime, config, **parts) -> Handled:
    """Classify an authorised update (A6) and answer it. Fixed replies are never remembered."""
    kind = classify(update, config.action_words)
    if isinstance(kind, TextOnly):
        return done([replies.text_only()], Outcome("fixed", detail="R-TEXT-ONLY"))
    if isinstance(kind, Command):
        text = replies.help_text(config.action_words, config.owner_time_zone)
        return done([text], Outcome("fixed", detail="R-HELP"))
    if isinstance(kind, ActionRequest):
        return refuse(
            update["message"]["text"].strip(), "keyword", kind.matched_word,
            config=config, answer_model=parts["answer_model"],
        )
    return handle(kind.text, now=now, config=config, **parts)


def handle(
    message_text: str, *, now: datetime, config, store, plan_model, answer_model, memory,
    config_dir: Path | None = None,
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
        return done(replies.too_much(), Outcome("too_much"))
    try:
        reply = plan_model.complete(PLAN_SYSTEM, fitted[0], PLAN_SCHEMA)
    except ModelError as exc:
        return _failed(_category(exc))
    plan_tokens = (reply.input_tokens, reply.output_tokens)
    plan = parse_plan(reply.text)
    if plan is not None and plan.action_request:
        return refuse(
            message_text, "model_flag", "", config=config, answer_model=answer_model,
            plan_tokens=plan_tokens,
        )

    valid: list[ValidChoice] = []
    dropped: list[Dropped] = []
    results: list[QueryResult] = []
    try:
        if plan is not None:
            valid, dropped, from_db = _read(plan, store, now, limits)
            results = _merge(valid, from_db, config_dir or DEFAULT_CONFIG_PATH.parent)
    except StoreError as exc:
        return done(replies.database(), Outcome("database", detail=type(exc).__name__,
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
        return done(replies.too_much(), Outcome("too_much", **seen))
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
    return done(parts, Outcome("answered", links_removed=removed, messages=len(parts), **seen))


def _read(plan, store, now: datetime, limits):
    """Validate the plan and read the database entries: valid, dropped, database results.

    Symbols are looked up only when a choice carries one, and then in the same transaction
    as the reads. With none, no lookup is made, and no connection at all unless a database
    entry is chosen."""

    def check(known):
        return validate(
            plan.choices,
            known_symbols=known,
            today_local=now.date(),
            zone=now.tzinfo,
            max_span_days=limits.max_span_days,
            max_queries=limits.max_queries_per_question,
        )

    symbols = symbols_to_check(plan.choices)
    if symbols:
        return store.read_question(symbols, check)
    valid, dropped = check(set())
    database = [c for c in valid if c.name != "settings"]
    return valid, dropped, store.run(database) if database else []


def _merge(valid: list[ValidChoice], from_db, config_dir: Path) -> list[QueryResult]:
    """Database results and `settings` from the files (A16), in the order of the choices."""
    from_db = iter(from_db)
    results: list[QueryResult] = []
    for choice in valid:
        if choice.name == "settings":
            given = {"section": choice.section} if choice.section else {}
            data = settings_view.read(config_dir, choice.section or None)
            results.append(QueryResult("settings", given, [data], False))
        else:
            results.append(next(from_db))
    return results


def _category(exc: ModelError) -> str:
    if isinstance(exc, ModelRefused):
        return "refused"
    if isinstance(exc, ModelRejected | ModelKeyRejected):
        return "rejected"
    if isinstance(exc, ModelTruncated):
        return "truncated"
    return "unavailable"


def _failed(category: str, **seen) -> Handled:
    text = replies.model_failed(replies.FAILURE_TEXT[category])
    return done([text], Outcome("model_failed", category=category, **seen))


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
