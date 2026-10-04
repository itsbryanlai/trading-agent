"""One Portfolio Manager run (specs/008-portfolio-manager research P1, P3, P10, P14).

Market-open check -> read the state -> quote phase (paced, with a deadline) -> candidates
-> one model call -> check -> market-open recheck -> one write. The service decides
nothing itself: the model proposes, `answer.check` disposes, and only checked decisions
reach the store. Database errors are not caught here: they reach `__main__` as exit 3.

`clock` is read for the run's start, for each quote's arrival (its freshness is judged at
that time, analyze F1) and before the write. Nothing here reads the system clock.

Seams for User Story 4 (tasks T032), the failure paths not yet built: a rejected quote key
(`KeyRejected` propagates), a model error (propagates), `no_fresh_quotes` (a run where no
candidate has a fresh quote ends quietly, with a note), and `internal_error`. Only the
three failures below, which the flow can't continue without, are handled here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from trading_agent.llm.ports import ModelClient
from trading_agent.llm.settings import ModelSettings
from trading_agent.portfolio_manager import answer, inputs, prompt
from trading_agent.portfolio_manager.store import Store
from trading_agent.reference.provider import KeyRejected, ProviderError, Quote
from trading_agent.risk import calendar

log = logging.getLogger("trading_agent.portfolio_manager")

NO_ACCOUNT_SNAPSHOT = "no_account_snapshot"
UNUSABLE_ANSWER = "unusable_answer"
WINDOW_CLOSED = "window_closed"


class QuoteSource(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...


class Settings(Protocol):
    """The part of config/portfolio_manager.yaml the run uses (config.py satisfies it)."""

    quote_max_age_minutes: int
    finnhub_calls_per_minute: int
    quote_phase_seconds: int
    journal_entries: int
    reasoning_max_chars: int
    model: ModelSettings


@dataclass
class RunOutcome:
    decisions: tuple[answer.CheckedDecision, ...] = ()
    drops: tuple[answer.Drop, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()  # (symbol, quote_missing | quote_stale)
    failure: str | None = None
    market_closed: bool = False  # outside the regular session: nothing was done
    note: str = ""  # why a run that wrote nothing was not a failure
    input_tokens: int | None = None
    output_tokens: int | None = None
    input_chars: int = 0
    candidates: list[str] = field(default_factory=list)


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
) -> RunOutcome:
    run_start = clock()
    if not calendar.market_open(run_start):
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
        _run(run_start, clock, config, store, quotes, model, sleep, outcome)
    except _Failed as failed:
        outcome.failure = failed.category
        outcome.decisions = ()
    return outcome


def _run(run_start, clock, config, store, quotes, model, sleep, outcome: RunOutcome) -> None:
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
    built = inputs.build_candidates(data, fetched, run_start=run_start, max_age=max_age)
    outcome.skipped = built.skipped
    outcome.candidates = [c.symbol for c in built.candidates]
    log.info(
        "portfolio_manager: %d fresh quotes; skipped: %s",
        len(built.candidates),
        ", ".join(f"{s} ({why})" for s, why in built.skipped) or "none",
    )
    if not built.candidates:
        # User Story 4 turns this into the failure `no_fresh_quotes`.
        outcome.note = "no candidate has a fresh quote"
        log.info("portfolio_manager: nothing to decide (%s)", outcome.note)
        return

    user = prompt.build_user_document(
        run_start, built.account, built.positions, built.candidates, built.journal
    )
    outcome.input_chars = len(user)
    reply = model.complete(prompt.SYSTEM_PROMPT, user, answer.ANSWER_SCHEMA)
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
    if not calendar.market_open(clock()):
        # The run can outlast the session: a decision written after the close would be
        # evaluated against a closed market (research P10).
        log.error("portfolio_manager: %s: the market closed before the write", WINDOW_CLOSED)
        raise _Failed(WINDOW_CLOSED)
    store.write(checked.decisions)  # not caught: a database error is exit 3
    outcome.decisions = checked.decisions
    log.info("portfolio_manager: wrote %d decision(s)", len(checked.decisions))


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
        except KeyRejected:
            raise  # User Story 4: quote_key_rejected
        except ProviderError as exc:
            log.warning("portfolio_manager: quote for %s missing: %s", symbol, type(exc).__name__)
            continue
        fetched[symbol] = inputs.FetchedQuote(quote, clock())
    return fetched
