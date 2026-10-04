"""What the PM works from, and what it shows the model (specs/008-portfolio-manager
research P6, P7; data-model.md "In-memory entities"). Pure: it reads no clock (`run_start`
and each quote's fetch time are arguments), no database and no network.

`Inputs` is what the store reads. `build_candidates` joins it with the quotes fetched
and returns the symbols the model may decide, each with its reports, current weight and
earlier decisions today, plus the identifier map (`R1` -> database report id) that the
answer checker holds the model to.

Size (spec User Story 3): `build_candidates` cuts each rationale and journal summary to
its limit; if the rendered document is still over `max_input_chars`, `Built.keeping`
drops whole candidates from the end (prompt.fit_user_document drives it).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from trading_agent.portfolio_manager import freshness, text
from trading_agent.reference.provider import Quote

FULL_EXIT = "full exit"
TARGET_WEIGHT = "target weight"
PRIMARY = "primary"
SECONDARY = "secondary"
UNMARKED = "unmarked"
INPUT_LIMIT = "input_limit"  # a skip reason, with freshness.QUOTE_MISSING and QUOTE_STALE

# --- what the store reads ---------------------------------------------------------


@dataclass(frozen=True)
class ReportRecord:
    id: str
    agent: str
    symbol: str | None
    direction: str
    conviction: int | None
    suggested_size_pct: Decimal | None
    sources: tuple[dict, ...]
    rationale_md: str
    generated_at: datetime
    expires_at: datetime
    consumed: bool  # already cited by a decision (reports_with_status)


@dataclass(frozen=True)
class PositionRecord:
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal


@dataclass(frozen=True)
class AccountRecord:
    equity: Decimal
    cash: Decimal
    taken_at: datetime


@dataclass(frozen=True)
class JournalRecord:
    trading_day: date
    equity_open: Decimal
    equity_close: Decimal
    summary_md: str


@dataclass(frozen=True)
class DecisionRecord:
    """One of the PM's own decisions today. Never its reasoning (spec Clarifications Q3)."""

    symbol: str
    direction: str
    size_pct: Decimal
    generated_at: datetime


@dataclass(frozen=True)
class Inputs:
    reports: tuple[ReportRecord, ...]
    positions: tuple[PositionRecord, ...]
    account: AccountRecord | None
    journal: tuple[JournalRecord, ...]
    earlier_decisions: tuple[DecisionRecord, ...]


@dataclass(frozen=True)
class FetchedQuote:
    """A quote (None when the fetch failed) and the time it arrived: its freshness is
    judged at that time, not at the run's start (analyze F1)."""

    quote: Quote | None
    fetched_at: datetime


# --- what the model is shown --------------------------------------------------------


@dataclass(frozen=True)
class SourceView:
    title: str
    publisher: str
    published_at: str
    relevance: str  # primary | secondary | unmarked


@dataclass(frozen=True)
class ReportView:
    run_id: str  # R1, R2, ...: the only identifier the model sees
    agent: str
    symbol: str
    direction: str
    conviction: int | None
    suggested_size_pct: Decimal | None
    size_meaning: str  # "full exit" for a sell at 0, else "target weight"
    already_decided_on: bool
    primary_sources: int
    secondary_sources: int
    sources: tuple[SourceView, ...]
    rationale: str
    generated_at: datetime


@dataclass(frozen=True)
class EarlierDecision:
    direction: str
    size_pct: Decimal
    at: datetime


@dataclass(frozen=True)
class Candidate:
    symbol: str
    quote: Decimal
    quote_time: datetime
    current_weight_pct: Decimal
    reports: tuple[ReportView, ...]
    earlier_decisions: tuple[EarlierDecision, ...]


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    quote: Decimal | None
    weight_pct: Decimal | None


@dataclass(frozen=True)
class JournalView:
    trading_day: date
    equity_open: Decimal
    equity_close: Decimal
    summary: str


# --- what the checker holds the model to ----------------------------------------------


@dataclass(frozen=True)
class ReportRef:
    report_id: str  # the database id
    symbol: str
    direction: str


@dataclass(frozen=True)
class SymbolFacts:
    quote: Decimal
    quote_time: datetime
    current_weight_pct: Decimal


@dataclass(frozen=True)
class Given:
    """Everything `answer.check` needs to know about this run."""

    refs: Mapping[str, ReportRef]
    symbols: Mapping[str, SymbolFacts]
    reasoning_max_chars: int


@dataclass(frozen=True)
class Built:
    candidates: tuple[Candidate, ...]
    positions: tuple[PositionView, ...]
    skipped: tuple[tuple[str, str], ...]  # (symbol, quote_missing | quote_stale | input_limit)
    refs: Mapping[str, ReportRef]
    account: AccountRecord
    journal: tuple[JournalView, ...]

    def keeping(self, count: int) -> Built:
        """The first `count` candidates only. The rest are skipped as `input_limit` and
        their report ids are withdrawn, so the model can't cite what it was never shown.
        Positions and the account stay."""
        if count >= len(self.candidates):
            return self
        kept = self.candidates[:count]
        symbols = {c.symbol for c in kept}
        dropped = [(c.symbol, INPUT_LIMIT) for c in reversed(self.candidates[count:])]
        return replace(
            self,
            candidates=kept,
            skipped=(*self.skipped, *dropped),
            refs={k: v for k, v in self.refs.items() if v.symbol in symbols},
        )

    def given(self, *, reasoning_max_chars: int) -> Given:
        return Given(
            refs=self.refs,
            symbols={
                c.symbol: SymbolFacts(c.quote, c.quote_time, c.current_weight_pct)
                for c in self.candidates
            },
            reasoning_max_chars=reasoning_max_chars,
        )


# --- the logic --------------------------------------------------------------------------


def _live_reports(data: Inputs, run_start: datetime) -> list[ReportRecord]:
    return [
        r
        for r in data.reports
        if r.direction != "no_action" and r.symbol is not None and r.expires_at > run_start
    ]


def _newest_first(reports: list[ReportRecord]) -> list[str]:
    """Symbols with a live report, newest report first (ties by symbol)."""
    newest: dict[str, datetime] = {}
    for r in reports:
        newest[r.symbol] = max(newest.get(r.symbol, r.generated_at), r.generated_at)
    return sorted(newest, key=lambda s: (-newest[s].timestamp(), s))


def considered_symbols(data: Inputs, run_start: datetime) -> list[str]:
    """Symbols with at least one unexpired, actionable report, newest report first."""
    return _newest_first(_live_reports(data, run_start))


def symbols_to_quote(data: Inputs, run_start: datetime) -> list[str]:
    """Symbols under consideration first (newest report first), then held symbols with
    no report (research P3)."""
    considered = considered_symbols(data, run_start)
    held = [p.symbol for p in data.positions if p.symbol not in considered]
    return [*considered, *held]


def build_candidates(
    data: Inputs,
    quotes: Mapping[str, FetchedQuote],
    *,
    run_start: datetime,
    max_age: timedelta,
    rationale_max_chars: int | None = None,
    journal_summary_max_chars: int | None = None,
) -> Built:
    if data.account is None:
        raise ValueError("an account snapshot is required")
    equity = data.account.equity
    live = _live_reports(data, run_start)

    fresh: dict[str, Quote] = {}
    skipped: list[tuple[str, str]] = []
    for symbol in _newest_first(live):
        fetched = quotes.get(symbol)
        reason = (
            freshness.QUOTE_MISSING
            if fetched is None
            else freshness.staleness_reason(fetched.quote, fetched.fetched_at, max_age)
        )
        if reason is None:
            fresh[symbol] = fetched.quote
        else:
            skipped.append((symbol, reason))

    held = {p.symbol: p.qty for p in data.positions}
    ordered = sorted((r for r in live if r.symbol in fresh), key=_report_order)
    run_ids = {r.id: f"R{n}" for n, r in enumerate(ordered, start=1)}
    refs = {run_ids[r.id]: ReportRef(r.id, r.symbol, r.direction) for r in ordered}

    candidates = []
    for symbol in _newest_first(ordered):
        quote = fresh[symbol].current
        weight = held.get(symbol, Decimal(0)) * quote / equity * 100
        candidates.append(
            Candidate(
                symbol=symbol,
                quote=quote,
                quote_time=fresh[symbol].timestamp,
                current_weight_pct=weight,
                reports=tuple(
                    _view(r, run_ids[r.id], rationale_max_chars)
                    for r in ordered
                    if r.symbol == symbol
                ),
                earlier_decisions=tuple(
                    EarlierDecision(d.direction, d.size_pct, d.generated_at)
                    for d in sorted(data.earlier_decisions, key=lambda d: d.generated_at)
                    if d.symbol == symbol
                ),
            )
        )

    return Built(
        candidates=tuple(candidates),
        positions=tuple(_position_view(p, quotes, equity, max_age) for p in data.positions),
        skipped=tuple(skipped),
        refs=refs,
        account=data.account,
        journal=tuple(
            JournalView(
                j.trading_day,
                j.equity_open,
                j.equity_close,
                _cut(j.summary_md, journal_summary_max_chars),
            )
            for j in data.journal
        ),
    )


def _report_order(r: ReportRecord):
    return (r.symbol, r.generated_at, r.id)


def _cut(value: str, limit: int | None) -> str:
    return value if limit is None else text.cap(value, limit)


def _view(r: ReportRecord, run_id: str, rationale_max_chars: int | None) -> ReportView:
    sources = tuple(_source(s) for s in r.sources)
    full_exit = r.direction == "sell" and r.suggested_size_pct == 0
    return ReportView(
        run_id=run_id,
        agent=r.agent,
        symbol=r.symbol,
        direction=r.direction,
        conviction=r.conviction,
        suggested_size_pct=r.suggested_size_pct,
        size_meaning=FULL_EXIT if full_exit else TARGET_WEIGHT,
        already_decided_on=r.consumed,
        primary_sources=sum(s.relevance == PRIMARY for s in sources),
        secondary_sources=sum(s.relevance == SECONDARY for s in sources),
        sources=sources,
        rationale=_cut(r.rationale_md, rationale_max_chars),
        generated_at=r.generated_at,
    )


def _source(raw) -> SourceView:
    raw = raw if isinstance(raw, dict) else {}
    relevance = raw.get("relevance")
    return SourceView(
        title=str(raw.get("title") or ""),
        publisher=str(raw.get("publisher") or ""),
        published_at=str(raw.get("published_at") or ""),
        relevance=relevance if relevance in (PRIMARY, SECONDARY) else UNMARKED,
    )


def _position_view(p, quotes, equity, max_age) -> PositionView:
    fetched = quotes.get(p.symbol)
    fresh = (
        fetched is not None
        and freshness.staleness_reason(fetched.quote, fetched.fetched_at, max_age) is None
    )
    if not fresh:
        return PositionView(p.symbol, p.qty, p.avg_entry_price, None, None)
    price = fetched.quote.current
    return PositionView(p.symbol, p.qty, p.avg_entry_price, price, p.qty * price / equity * 100)
