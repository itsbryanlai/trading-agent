"""One Portfolio Manager run (specs/008-portfolio-manager research P1, P3, P10, P14).

Market-open check -> read the state -> quote phase (paced, with a deadline) -> candidates
-> one model call -> check -> market-open recheck -> one write. The service decides
nothing itself: the model proposes, `answer.check` disposes, and only checked decisions
reach the store. Database errors are not caught here: they reach `__main__` as exit 3.

`clock` is read for the run's start, for each quote's arrival (its freshness is judged at
that time, analyze F1) and before the write. Nothing here reads the system clock.

Failures (research P9): every failure is one category on `RunOutcome.failure` and writes
nothing. A database error is not a category: `StoreError` escapes, to be exit 3. Any other
unexpected exception is `internal_error`; only its type is logged, never its message.

A dry run (`dry_run=True`, research P2) does everything but the write, and neither checks
that the market is open: it reports the would-be decisions on the outcome instead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from trading_agent.llm.ports import (
    ModelClient,
    ModelError,
    ModelKeyRejected,
    ModelRefused,
    ModelRejected,
    ModelTruncated,
    ModelUnavailable,
)
from trading_agent.llm.settings import ModelSettings
from trading_agent.portfolio_manager import answer, inputs, prompt
from trading_agent.portfolio_manager.store import Store, StoreError
from trading_agent.reference.provider import KeyRejected, ProviderError, Quote
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.portfolio_manager")

NO_ACCOUNT_SNAPSHOT = "no_account_snapshot"
QUOTE_KEY_REJECTED = "quote_key_rejected"
NO_FRESH_QUOTES = "no_fresh_quotes"
MODEL_KEY_REJECTED = "model_key_rejected"
MODEL_REJECTED_REQUEST = "model_rejected_request"
MODEL_UNAVAILABLE = "model_unavailable"
MODEL_REFUSED = "model_refused"
MODEL_TRUNCATED = "model_truncated"
UNUSABLE_ANSWER = "unusable_answer"
WINDOW_CLOSED = "window_closed"
INTERNAL_ERROR = "internal_error"

_MODEL_CATEGORIES = (
    (ModelKeyRejected, MODEL_KEY_REJECTED),
    (ModelRejected, MODEL_REJECTED_REQUEST),
    (ModelUnavailable, MODEL_UNAVAILABLE),
    (ModelRefused, MODEL_REFUSED),
    (ModelTruncated, MODEL_TRUNCATED),
)


class QuoteSource(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...


class Settings(Protocol):
    """The part of config/portfolio_manager.yaml the run uses (config.py satisfies it)."""

    quote_max_age_minutes: int
    finnhub_calls_per_minute: int
    quote_phase_seconds: int
    journal_entries: int
    journal_summary_max_chars: int
    rationale_max_chars: int
    reasoning_max_chars: int
    max_input_chars: int
    model: ModelSettings


@dataclass
class RunOutcome:
    decisions: tuple[answer.CheckedDecision, ...] = ()
    drops: tuple[answer.Drop, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()  # (symbol, quote_missing | quote_stale | input_limit)
    failure: str | None = None
    market_closed: bool = False  # outside the regular session: nothing was done
    note: str = ""  # why a run that wrote nothing was not a failure
    input_tokens: int | None = None
    output_tokens: int | None = None
    input_chars: int = 0
    candidates: list[str] = field(default_factory=list)
    built: inputs.Built | None = None  # what the model was given; the dry run prints it


class _Failed(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def run(
    *,
    clock: Callable[[], datetime],
    config: Settings,
    store: Store,
    quotes: QuoteSource,
    model: ModelClient,
    sleep: Callable[[float], None],
    dry_run: bool = False,
) -> RunOutcome:
    run_start = clock()
    if not dry_run and not calendar.market_open(run_start):
        log.info("portfolio_manager: market closed; nothing to do")
        return RunOutcome(market_closed=True)
    log.info(
        "portfolio_manager: run started (prompt v%s, provider %s, model %s)",
        prompt.PROMPT_VERSION,
        config.model.provider,
        config.model.name,
    )
    outcome = RunOutcome()
    try:
        _run(run_start, clock, config, store, quotes, model, sleep, outcome, dry_run)
    except _Failed as failed:
        outcome.failure = failed.category
        outcome.decisions = ()
    except StoreError:
        raise  # exit 3, not a category
    except Exception as exc:  # anything unanticipated is still a recorded failure
        log.error("portfolio_manager: %s: %s", INTERNAL_ERROR, type(exc).__name__)
        outcome.failure = INTERNAL_ERROR
        outcome.decisions = ()
    return outcome


def _run(
    run_start, clock, config, store, quotes, model, sleep, outcome: RunOutcome, dry_run: bool
) -> None:
    data = store.read_inputs(run_start, journal_entries=config.journal_entries)
    if data.account is None or data.account.equity <= 0:
        log.error("portfolio_manager: %s", NO_ACCOUNT_SNAPSHOT)
        raise _Failed(NO_ACCOUNT_SNAPSHOT)

    considered = inputs.considered_symbols(data, run_start)
    live = [r for r in data.reports if r.symbol in considered and r.expires_at > run_start]
    log.info(
        "portfolio_manager: %d reports on %d symbols (%d already decided on); %d held",
        len(live),
        len(considered),
        sum(r.consumed for r in live),
        len(data.positions),
    )
    if not considered:
        outcome.note = "no unexpired report"
        log.info("portfolio_manager: nothing to decide (%s)", outcome.note)
        return

    fetched = _quote_phase(inputs.symbols_to_quote(data, run_start), clock, config, quotes, sleep)
    max_age = timedelta(minutes=config.quote_max_age_minutes)
    built = inputs.build_candidates(
        data,
        fetched,
        run_start=run_start,
        max_age=max_age,
        rationale_max_chars=config.rationale_max_chars,
        journal_summary_max_chars=config.journal_summary_max_chars,
    )
    if not built.candidates:
        _log_quotes(built)
        outcome.skipped = built.skipped
        log.error("portfolio_manager: %s: no candidate has a usable quote", NO_FRESH_QUOTES)
        raise _Failed(NO_FRESH_QUOTES)
    # The size limit comes before anything is logged or given to the model: a symbol dropped
    # for size is skipped like any other, and its report ids are withdrawn.
    user, built = prompt.fit_user_document(run_start, built, config.max_input_chars)
    outcome.skipped = built.skipped
    outcome.candidates = [c.symbol for c in built.candidates]
    outcome.built = built
    _log_quotes(built)
    if not built.candidates:
        # Only the account and positions fit, which max_input_chars is meant to rule out.
        log.error("portfolio_manager: %s: no candidate fits max_input_chars", INTERNAL_ERROR)
        raise _Failed(INTERNAL_ERROR)

    outcome.input_chars = len(user)
    reply = _ask(model, user)
    outcome.input_tokens, outcome.output_tokens = reply.input_tokens, reply.output_tokens
    log.info(
        "portfolio_manager: model used %s input and %s output tokens",
        reply.input_tokens,
        reply.output_tokens,
    )

    checked = answer.check(reply.text, built.given(reasoning_max_chars=config.reasoning_max_chars))
    if checked.unusable:
        log.error("portfolio_manager: %s: answer not in the required shape", UNUSABLE_ANSWER)
        raise _Failed(UNUSABLE_ANSWER)
    outcome.drops = checked.drops
    log.info(
        "portfolio_manager: %d decisions received, %d accepted, %d dropped",
        checked.received,
        len(checked.decisions),
        len(checked.drops),
    )
    for drop in checked.drops:
        log.info(
            "portfolio_manager: dropped decision %d (%s): %s",
            drop.index,
            drop.symbol or "-",
            drop.reason,
        )

    if not checked.decisions:
        outcome.note = "no decision survived" if checked.drops else "the model proposed none"
        log.info("portfolio_manager: nothing to decide (%s)", outcome.note)
        return
    if dry_run:
        outcome.decisions = checked.decisions
        log.info("portfolio_manager: dry run; %d decision(s) not written", len(checked.decisions))
        return
    if not calendar.market_open(clock()):
        # The run can outlast the session: a decision written after the close would be
        # evaluated against a closed market (research P10).
        log.error("portfolio_manager: %s: the market closed before the write", WINDOW_CLOSED)
        raise _Failed(WINDOW_CLOSED)
    store.write(checked.decisions)  # not caught: a database error is exit 3
    outcome.decisions = checked.decisions
    log.info("portfolio_manager: wrote %d decision(s)", len(checked.decisions))


def _log_quotes(built: inputs.Built) -> None:
    log.info(
        "portfolio_manager: %d fresh quotes; skipped: %s",
        len(built.candidates),
        ", ".join(f"{s} ({why})" for s, why in built.skipped) or "none",
    )


def _ask(model: ModelClient, user: str):
    """One call, no retry and no second provider. A provider's error text is never logged:
    its type and HTTP status are enough."""
    try:
        return model.complete(prompt.SYSTEM_PROMPT, user, answer.ANSWER_SCHEMA)
    except ModelError as exc:
        category = next((c for cls, c in _MODEL_CATEGORIES if isinstance(exc, cls)), None)
        if category is None:
            raise  # a ModelError of no known kind: internal_error
        status = f" (HTTP {exc.status})" if exc.status is not None else ""
        log.error("portfolio_manager: %s: %s%s", category, type(exc).__name__, status)
        raise _Failed(category) from None


def _quote_phase(symbols, clock, config, quotes, sleep) -> dict[str, inputs.FetchedQuote]:
    """One quote per symbol, paced, until the phase deadline. A symbol whose fetch failed,
    or that was not reached, has no entry: `build_candidates` reports it as missing."""
    interval = 60 / config.finnhub_calls_per_minute
    deadline = timedelta(seconds=config.quote_phase_seconds)
    phase_start = clock()
    fetched: dict[str, inputs.FetchedQuote] = {}
    for n, symbol in enumerate(symbols):
        if n:
            sleep(interval)
        if clock() - phase_start >= deadline:
            break  # the rest count as missing
        try:
            quote = quotes.get_quote(symbol)
        except KeyRejected as exc:
            log.error("portfolio_manager: %s: %s", QUOTE_KEY_REJECTED, type(exc).__name__)
            raise _Failed(QUOTE_KEY_REJECTED) from None
        except ProviderError as exc:
            log.warning("portfolio_manager: quote for %s missing: %s", symbol, type(exc).__name__)
            continue
        fetched[symbol] = inputs.FetchedQuote(quote, clock())
    return fetched
