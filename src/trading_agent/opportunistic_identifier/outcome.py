"""What a run reports: its counts, its outcome, and the closed sets of reasons and failure
categories (contracts/oi-interface.md "Closed sets"). Pure data, shared by the fetch and the
run so neither imports the other."""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_agent.opportunistic_identifier import answer as a
from trading_agent.opportunistic_identifier import rotation, screen
from trading_agent.opportunistic_identifier.answer import ReportRow
from trading_agent.opportunistic_identifier.screen import Skip
from trading_agent.reference.normalize import ALL_REASONS as ALL_NORMALIZE_REASONS
from trading_agent.risk import rules

# Skip reasons that come from the fetch itself.
NOT_FETCHED = "not_fetched"
PROVIDER_UNAVAILABLE = "provider_unavailable"
NOT_PERMITTED = "not_permitted"
RATE_LIMITED = "rate_limited"
FETCH_FAILURES = frozenset({NOT_FETCHED, PROVIDER_UNAVAILABLE, NOT_PERMITTED, RATE_LIMITED})

# Every per-name skip reason (contracts/oi-interface.md "Closed sets"): the fetch's, the
# screen's own, every `reference.normalize` failure and the universe rules.
SKIP_REASONS = (
    FETCH_FAILURES
    | {
        screen.STALE_QUOTE,
        screen.MISSING_PRICE,
        screen.MISSING_52_WEEK_HIGH,
        screen.INCONSISTENT_52_WEEK_RANGE,
        screen.MISSING_FUNDAMENTALS,
        screen.IMPLAUSIBLE_MOVE,
    }
    | set(ALL_NORMALIZE_REASONS)
    | {
        rules.UNIVERSE_LISTING,
        rules.UNIVERSE_MARKET_CAP,
        rules.UNIVERSE_DOLLAR_VOLUME,
        rules.UNIVERSE_SHARE_PRICE,
    }
)

# Quiet no_action reasons (exit 0).
EMPTY_SCAN_UNIVERSE = "empty_scan_universe"
EMPTY_SHORTLIST = "empty_shortlist"
NOTHING_ARGUED = "nothing_argued"
ALL_DROPPED = "all_dropped"

# Failure categories (a failure no_action row, exit 1).
SYMBOL_LIST_UNAVAILABLE = "symbol_list_unavailable"
MARKET_DATA_UNAVAILABLE = "market_data_unavailable"
INPUT_TOO_LARGE = "input_too_large"
MODEL_KEY_REJECTED = "model_key_rejected"
MODEL_REJECTED_REQUEST = "model_rejected_request"
MODEL_UNAVAILABLE = "model_unavailable"
MODEL_REFUSED = "model_refused"
MODEL_TRUNCATED = "model_truncated"
UNUSABLE_ANSWER = "unusable_answer"
INTERNAL_ERROR = "internal_error"
# Not a row category: the close passed before the write, so nothing is written (exit 5).
WINDOW_CLOSED = "window_closed"

SENTENCES = {
    EMPTY_SCAN_UNIVERSE: "The scan list is empty.",
    EMPTY_SHORTLIST: "No name in the slice was eligible and not already open.",
    NOTHING_ARGUED: "The model proposed nothing worth arguing.",
    ALL_DROPPED: "Every proposal the model made was dropped by the checks.",
    SYMBOL_LIST_UNAVAILABLE: "The list of US-listed symbols could not be fetched.",
    MARKET_DATA_UNAVAILABLE: "The market-data key was rejected, or no name could be fetched.",
    INPUT_TOO_LARGE: "The model input was over the size limit, so no call was made.",
    MODEL_KEY_REJECTED: "The model provider rejected the key.",
    MODEL_REJECTED_REQUEST: "The model provider rejected the request.",
    MODEL_UNAVAILABLE: "The model provider could not be reached or did not answer in time.",
    MODEL_REFUSED: "The model declined to answer.",
    MODEL_TRUNCATED: "The model's answer hit the output limit and was discarded.",
    UNUSABLE_ANSWER: "The model's answer was not in the required shape.",
    INTERNAL_ERROR: "The Opportunistic Identifier hit an unexpected error.",
}


class Failed(Exception):
    """A failure category: the run writes one `no_action` row naming it."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass
class RunCounts:
    in_slice: int = 0
    fetched: int = 0  # names whose quote was obtained
    skipped: dict[str, int] = field(default_factory=dict)
    already_open: int = 0
    eligible: int = 0
    shortlisted: int = 0
    proposed: int = 0
    written: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class RunOutcome:
    rows: list[ReportRow] = field(default_factory=list)
    counts: RunCounts = field(default_factory=RunCounts)
    slice: rotation.ScanSlice | None = None
    skips: list[Skip] = field(default_factory=list)
    shortlist: tuple[screen.Candidate, ...] = ()
    drops: tuple[a.Drop, ...] = ()
    failure: str | None = None  # a failure category (or WINDOW_CLOSED, which writes nothing)
    note: str = ""  # why a quiet no_action was written
    skipped: bool = False  # outside the trading window: nothing read, fetched or written

    def skip(self, skip: Skip) -> None:
        self.skips.append(skip)
        self.counts.skipped[skip.reason] = self.counts.skipped.get(skip.reason, 0) + 1
