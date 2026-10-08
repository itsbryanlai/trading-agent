"""One journal run with a fake store and fake quotes (specs/012 T009)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.fakes.journal_store import FakeJournalStore, empty_reads
from tests.fakes.market_data import FakeMarketData
from trading_agent.journal.config import JournalConfig
from trading_agent.journal.model import Book, BookResult, Holding
from trading_agent.journal.service import run
from trading_agent.journal.state import encode
from trading_agent.risk import calendar

D = Decimal
FRIDAY = date(2026, 10, 9)
CFG = JournalConfig(5, 20, 480, 5)
NOW = datetime(2026, 10, 9, 22, 30, tzinfo=UTC)


def utc(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


def snapshots(day_open="100000.00", day_close="100500.00", taken=None):
    return {
        "snapshot_open": {"taken_at": utc(10, 9, 13, 0), "equity": D(day_open)},
        "snapshot_close": {
            "taken_at": taken or utc(10, 9, 19, 30),
            "equity": D(day_close),
        },
    }


def market_for(day=FRIDAY, **quotes):
    clock = Clock()
    market = FakeMarketData(clock=clock.monotonic, quote_time=utc(day.month, day.day, 19, 59))
    for symbol, close in quotes.items():
        market.add(symbol, current=close)
    return market, clock


def report(id_, agent, symbol, direction="buy", size=5, at=None):
    return {
        "id": id_,
        "agent": agent,
        "generated_at": at or utc(10, 9, 15),
        "symbol": symbol,
        "direction": direction,
        "suggested_size_pct": None if direction == "no_action" else D(size),
    }


def previous(day, equity_close="100000.00", **agents):
    """A previous row; agents map to (index, started_on, {symbol: (weight, ref, support)})."""
    results = {}
    for agent, (index, started, held) in agents.items():
        book = Book(
            agent,
            started,
            D(index),
            {s: Holding(s, D(w), D(ref), sup) for s, (w, ref, sup) in held.items()},
        )
        results[agent] = BookResult(book, D(0), None, 0, (), (), (), ())
    attribution = encode(
        day=day,
        previous_day=None,
        sessions_covered=1,
        missed_sessions=(),
        holding_sessions=5,
        summary_version="0.1",
        account_return=None,
        account_base="open",
        close_taken_at=None,
        results=results,
        usage={},
    )
    return {
        "trading_day": day,
        "equity_close": D(equity_close),
        "per_agent_attribution": attribution,
    }


def go(reads, market, clock, now=NOW, cfg=CFG):
    store = FakeJournalStore(reads)
    outcome = run(store, market, cfg, now=now, sleep=clock.sleep, monotonic=clock.monotonic)
    return outcome, store


# --- the gate ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "reason"),
    [
        (utc(10, 10, 22, 30), "not_a_session"),  # Saturday
        (utc(11, 26, 22, 30), "not_a_session"),  # Thanksgiving
        (utc(10, 9, 19, 59), "before_close"),
        (utc(11, 27, 17, 59), "before_close"),  # early close 13:00 ET = 18:00 UTC
    ],
)
def test_nothing_to_do_outside_a_closed_session(now, reason):
    market, clock = market_for()
    outcome, store = go(empty_reads(), market, clock, now=now)
    assert (outcome.status, outcome.reason) == ("nothing_to_do", reason)
    assert store.read_calls == [] and store.upserts == [] and market.calls == []


def test_the_early_close_day_runs_after_the_early_close():
    day = date(2026, 11, 27)
    market, clock = market_for(day, AAPL=100)
    reads = empty_reads(
        reports=[report(1, "research", "AAPL", at=utc(11, 27, 15))],
        snapshot_open={"taken_at": utc(11, 27, 14), "equity": D(1)},
        snapshot_close={"taken_at": utc(11, 27, 17), "equity": D(1)},
    )
    outcome, _ = go(reads, market, clock, now=utc(11, 27, 22, 30))
    assert outcome.status == "wrote"
    assert outcome.row.trading_day == day


# --- a first-ever run ----------------------------------------------------------------


def test_the_first_ever_run_writes_one_row_with_a_new_book():
    market, clock = market_for(AAPL="229.15")
    reads = empty_reads(reports=[report(1, "research", "AAPL")], **snapshots())
    outcome, store = go(reads, market, clock)
    assert outcome.status == "wrote"
    assert store.upserts == [outcome.row]
    row = outcome.row
    assert (row.trading_day, row.equity_open, row.equity_close) == (
        FRIDAY,
        D("100000.00"),
        D("100500.00"),
    )
    assert row.summary_md
    a = row.per_agent_attribution
    assert a["previous_trading_day"] is None
    assert (a["sessions_covered"], a["missed_sessions"]) == (1, [])
    assert a["account"] == {
        "return": "0.005000",  # 500 / 100000, from the open on a first-ever run
        "base": "open",
        "close_taken_at": "2026-10-09T19:30:00+00:00",
    }
    research = a["agents"]["research"]
    assert (research["started_on"], research["index"], research["day_return"]) == (
        "2026-10-09",
        "100.00000000",
        "0.000000",
    )
    assert research["holdings"] == {
        "AAPL": {"weight_pct": "5.000000", "ref_price": "229.15", "support_session": "2026-10-09"}
    }


def test_the_store_is_read_with_the_days_times_and_the_calendars_close():
    market, clock = market_for()
    _, store = go(empty_reads(**snapshots()), market, clock)
    ((day, open_at, close_at, previous_close_of),) = store.read_calls
    assert day == FRIDAY
    assert (open_at, close_at) == (calendar.open_time(FRIDAY), calendar.close_time(FRIDAY))
    assert previous_close_of is calendar.close_time


# --- a normal second day -------------------------------------------------------------


def test_a_second_day_values_the_previous_book_and_continues_its_index():
    market, clock = market_for(AAPL=204)
    prev = previous(
        date(2026, 10, 8),
        research=("104", date(2026, 10, 1), {"AAPL": ("10", "200", date(2026, 10, 7))}),
    )
    reads = empty_reads(previous=prev, **snapshots())
    outcome, _ = go(reads, market, clock)
    a = outcome.row.per_agent_attribution
    research = a["agents"]["research"]
    assert research["started_on"] == "2026-10-01"
    assert research["day_return"] == "0.002000"  # 10% x +2%
    assert research["index"] == "104.20800000"
    assert research["holdings"]["AAPL"]["ref_price"] == "204"
    assert (a["previous_trading_day"], a["sessions_covered"]) == ("2026-10-08", 1)
    assert a["account"]["base"] == "previous_close"
    assert a["account"]["return"] == "0.005000"  # 100500 / 100000 - 1


def test_the_account_return_is_null_when_its_base_is_zero():
    market, clock = market_for()
    reads = empty_reads(previous=previous(date(2026, 10, 8), equity_close="0"), **snapshots())
    a = go(reads, market, clock)[0].row.per_agent_attribution
    assert a["account"]["return"] is None
    reads = empty_reads(**snapshots(day_open="0", day_close="10"))
    a = go(reads, market, clock)[0].row.per_agent_attribution
    assert (a["account"]["return"], a["account"]["base"]) == (None, "open")


def test_the_rows_equity_columns_come_from_the_stores_open_and_close_snapshots():
    market, clock = market_for()
    outcome, _ = go(empty_reads(**snapshots("98765.43", "99999.99")), market, clock)
    assert (outcome.row.equity_open, outcome.row.equity_close) == (D("98765.43"), D("99999.99"))


# --- which agents have books ---------------------------------------------------------


def test_agents_in_only_one_of_the_two_sources_both_get_books():
    market, clock = market_for(AAPL=100, MSFT=50)
    prev = previous(
        date(2026, 10, 8),
        research=("120", date(2026, 10, 1), {"AAPL": ("10", "100", date(2026, 10, 8))}),
    )
    reads = empty_reads(
        previous=prev,
        reports=[report(1, "opportunistic_identifier", "MSFT", size=3)],
        **snapshots(),
    )
    agents = go(reads, market, clock)[0].row.per_agent_attribution["agents"]
    assert set(agents) == {"research", "opportunistic_identifier"}
    assert agents["research"]["index"] == "120.00000000"  # carried, no reports today
    assert agents["research"]["holdings"]["AAPL"]["weight_pct"] == "10.000000"
    oi = agents["opportunistic_identifier"]
    assert (oi["started_on"], oi["index"]) == ("2026-10-09", "100.00000000")
    assert oi["holdings"]["MSFT"]["weight_pct"] == "3.000000"


def test_an_agent_with_only_a_no_action_report_gets_an_empty_book():
    market, clock = market_for()
    reads = empty_reads(reports=[report(1, "research", None, "no_action")], **snapshots())
    agents = go(reads, market, clock)[0].row.per_agent_attribution["agents"]
    assert agents["research"]["holdings"] == {}


# --- what is priced ------------------------------------------------------------------


def test_each_held_symbol_and_each_buy_or_hold_target_is_fetched_once():
    market, clock = market_for(AAPL=100, MSFT=50, NVDA=10, TSLA=10, AMD=10)
    prev = previous(
        date(2026, 10, 8),
        research=("100", date(2026, 10, 1), {"AAPL": ("5", "100", date(2026, 10, 8))}),
        opportunistic_identifier=(
            "100",
            date(2026, 10, 1),
            {"AAPL": ("5", "100", date(2026, 10, 8))},
        ),
    )
    reads = empty_reads(
        previous=prev,
        reports=[
            report(1, "research", "MSFT", "buy"),
            report(2, "opportunistic_identifier", "MSFT", "hold"),
            report(3, "research", "NVDA", "sell", 0),  # unheld: no price needed
            report(4, "research", None, "no_action"),
            report(5, "research", "AAPL", "buy"),  # already held
        ],
        **snapshots(),
    )
    go(reads, market, clock)
    assert sorted(s for _, _, s in market.calls) == ["AAPL", "MSFT"]


def test_a_run_with_nothing_to_price_makes_no_quote_calls():
    market, clock = market_for()
    go(empty_reads(**snapshots()), market, clock)
    assert market.calls == []


def test_an_unpriced_symbol_is_carried_and_listed_but_the_run_still_writes():
    market, clock = market_for(AAPL=100)
    market.add("MSFT", current=50, quote_time=utc(10, 9, 23, 0))  # after-hours print
    prev = previous(
        date(2026, 10, 8),
        research=("100", date(2026, 10, 1), {"MSFT": ("5", "48", date(2026, 10, 8))}),
    )
    reads = empty_reads(previous=prev, reports=[report(1, "research", "AAPL")], **snapshots())
    outcome, _ = go(reads, market, clock)
    research = outcome.row.per_agent_attribution["agents"]["research"]
    assert outcome.status == "wrote"
    assert research["unpriced"] == ["MSFT"]
    assert research["holdings"]["MSFT"]["ref_price"] == "48"


# --- support sessions and late reports -----------------------------------------------


def test_a_report_after_thursdays_close_counts_from_friday_and_is_not_late():
    market, clock = market_for(AAPL=100)
    reads = empty_reads(
        previous=previous(date(2026, 10, 8)),
        reports=[report(1, "research", "AAPL", at=utc(10, 8, 20, 30))],
        **snapshots(),
    )
    research = go(reads, market, clock)[0].row.per_agent_attribution["agents"]["research"]
    assert research["holdings"]["AAPL"]["support_session"] == "2026-10-09"
    assert research["late_reports"] == 0


def test_a_report_before_its_sessions_close_counts_from_that_session():
    market, clock = market_for(AAPL=100)
    reads = empty_reads(
        previous=previous(date(2026, 10, 8)),
        reports=[report(1, "research", "AAPL", at=utc(10, 9, 14))],
        **snapshots(),
    )
    research = go(reads, market, clock)[0].row.per_agent_attribution["agents"]["research"]
    assert research["holdings"]["AAPL"]["support_session"] == "2026-10-09"


def test_a_weekend_report_with_a_missed_monday_counts_from_monday_and_is_late_on_tuesday():
    tuesday = date(2026, 10, 6)
    market, clock = market_for(tuesday, AAPL=100, MSFT=50)
    reads = empty_reads(
        previous=previous(date(2026, 10, 2)),
        reports=[
            report(1, "research", "AAPL", at=utc(10, 3, 15)),  # Saturday
            report(2, "research", "MSFT", at=utc(10, 6, 14)),  # Tuesday, before the close
        ],
        snapshot_open={"taken_at": utc(10, 6, 13), "equity": D(1)},
        snapshot_close={"taken_at": utc(10, 6, 19), "equity": D(1)},
    )
    outcome, _ = go(reads, market, clock, now=utc(10, 6, 22, 30))
    a = outcome.row.per_agent_attribution
    holdings = a["agents"]["research"]["holdings"]
    assert holdings["AAPL"]["support_session"] == "2026-10-05"
    assert holdings["MSFT"]["support_session"] == "2026-10-06"
    assert a["agents"]["research"]["late_reports"] == 1
    assert (a["sessions_covered"], a["missed_sessions"]) == (2, ["2026-10-05"])


def test_a_missed_session_extends_sessions_covered_and_is_listed():
    market, clock = market_for()
    reads = empty_reads(previous=previous(date(2026, 10, 7)), **snapshots())
    a = go(reads, market, clock)[0].row.per_agent_attribution
    assert (a["sessions_covered"], a["missed_sessions"]) == (2, ["2026-10-08"])
    assert a["previous_trading_day"] == "2026-10-07"


def test_the_holding_limit_applies_through_the_service():
    market, clock = market_for(AAPL=100)
    prev = previous(
        date(2026, 10, 8),
        research=("100", date(2026, 9, 1), {"AAPL": ("5", "100", date(2026, 10, 2))}),
    )
    a = go(empty_reads(previous=prev, **snapshots()), market, clock)[0].row.per_agent_attribution
    research = a["agents"]["research"]
    assert research["holdings"] == {}
    assert research["exited"]["holding_limit"] == ["AAPL"]
