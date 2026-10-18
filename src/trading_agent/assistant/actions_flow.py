"""The action refusal (research A8; ADR 0024 §2, ADR 0025 §2).

Two triggers lead here: the first word is an action word (`keyword`), or the plan call set
`action_request` (`model_flag`). Either way nothing is read: no store call of any kind, so a
refused message touches no data. One model call explains the refusal from a document of five
keys; if it fails, a fixed fallback is sent. Nothing is added to memory.
"""

from __future__ import annotations

from trading_agent.assistant import actions, replies
from trading_agent.assistant.answer import ANSWER_SCHEMA, parse_answer
from trading_agent.assistant.links import filter_links
from trading_agent.assistant.outcome import Handled, Outcome, done
from trading_agent.assistant.prompt import REFUSAL_SYSTEM
from trading_agent.llm.ports import ModelError


def refuse(
    message_text: str, trigger: str, word: str, *, config, answer_model, plan_tokens=None
) -> Handled:
    """The fixed first line, a blank line, then the model's explanation (or the fallback)."""
    shown = message_text[: config.limits.text_field_chars]
    document = actions.refusal_document(
        shown, trigger, word, config.action_words, replies.CONTROLS
    )
    tokens = None
    explanation = None
    try:
        reply = answer_model.complete(REFUSAL_SYSTEM, document, ANSWER_SCHEMA)
        tokens = (reply.input_tokens, reply.output_tokens)
        explanation = parse_answer(reply.text)
    except ModelError:
        pass
    if explanation is None:
        if trigger == "keyword":
            text = replies.refused_fallback(word, config.action_words, replies.CONTROLS)
        else:
            text = replies.refused_flag_fallback(config.action_words, replies.CONTROLS)
        result = "fallback"
    else:
        filtered, _ = filter_links(explanation, [])
        text = f"{replies.refused_line()}\n\n{filtered}"
        result = "explained"
    parts = replies.split(replies.cap(text, config.limits.max_reply_chars))
    return done(
        parts,
        Outcome(
            "refused", trigger=trigger, word=word if trigger == "keyword" else "",
            detail=result, plan_tokens=plan_tokens, refusal_tokens=tokens,
        ),
    )
