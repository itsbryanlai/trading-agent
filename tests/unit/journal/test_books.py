"""The book arithmetic (research J5, J6; spec US1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trading_agent.journal.books import advance, new_book, sessions_since
from trading_agent.journal.model import Book, BookResult, Holding, Price, ReportRow
from trading_agent.journal.state import decode_books, encode
from trading_agent.risk.calendar import previous_session

D = Decimal
DAY = date(2026, 10, 9)  # Friday
LIMIT = 5
EPS = D("1e-20")


def px(**closes) -> dict[str, Price]:
    return {
        s: Price(s, None if c is None else D(str(c)), None if c else "no_price")
        for s, c in closes.items()
    }


def hold(symbol, weight, ref, support=date(2026, 10, 8)) -> Holding:
    return Holding(symbol, D(str(weight)), D(str(ref)), support)


def book(*holdings, index="100", started=date(2026, 10, 1)) -> Book:
    return Book("research", started, D(index), {h.symbol: h for h in holdings})


_ids = iter(range(1, 10**9))


def report(symbol, direction="buy", size=5, *, support=DAY, late=False, minute=0) -> ReportRow:
    return ReportRow(
        next(_ids),
        "research",
        datetime(2026, 10, 9, 10, minute, tzinfo=UTC),
        symbol,
        direction,
        None if direction == "no_action" else D(str(size)),
        support,
        late,
    )


def run(b, reports, prices, day=DAY, limit=LIMIT) -> BookResult:
    return advance(b, reports, prices, day, limit, previous_session)


def weights(result) -> dict[str, Decimal]:
    return {s: h.weight_pct for s, h in result.book.holdings.items()}


def close_to(a, b, eps=EPS):
    assert abs(a - b) <= eps, (a, b)


# --- US1 acceptance scenarios --------------------------------------------------------


def test_scenario_1_a_new_agent_enters_at_today_s_close_with_no_return():
    r = run(
        new_book("research", DAY),
        [report("AAPL", size=5), report("MSFT", size=3)],
        px(AAPL=229.15, MSFT=400),
    )
    assert r.day_return == 0
    assert r.book.index == 100
    assert r.book.started_on == DAY
    assert weights(r) == {"AAPL": D(5), "MSFT": D(3)}
    assert r.book.holdings["AAPL"].ref_price == D("229.15")
    assert r.book.holdings["AAPL"].support_session == DAY


def test_scenario_2_a_holding_that_rose_two_percent_returns_point_two_percent():
    r = run(book(hold("AAPL", 10, 100), index="104"), [], px(AAPL=102))
    close_to(r.day_return, D("0.002"))
    close_to(r.book.index, D("104") * D("1.002"))
    assert r.book.holdings["AAPL"].ref_price == 102


def test_scenario_3_weights_over_100_are_scaled_down_and_the_factor_recorded():
    r = run(
        new_book("research", DAY),
        [report("AAPL", size=100, minute=1), report("MSFT", size=50, minute=2)],
        px(AAPL=10, MSFT=10),
    )
    assert r.scaled_by == D(100) / D(150)
    close_to(weights(r)["AAPL"], D(100) * D(100) / D(150))
    close_to(weights(r)["MSFT"], D(50) * D(100) / D(150))
    close_to(sum(weights(r).values()), D(100))


def test_no_scale_factor_when_the_weights_fit():
    r = run(new_book("research", DAY), [report("AAPL", size=100)], px(AAPL=10))
    assert r.scaled_by is None


def test_scenario_4_a_sell_to_zero_exits_the_symbol():
    r = run(
        book(hold("AAPL", 10, 100), hold("MSFT", 5, 50)),
        [report("AAPL", "sell", 0)],
        px(AAPL=100, MSFT=50),
    )
    assert set(r.book.holdings) == {"MSFT"}
    assert r.exited_sell == ("AAPL",)
    assert r.exited_holding_limit == ()


def test_scenario_5_a_holding_that_rose_ten_percent_drifts_to_10_89():
    r = run(book(hold("AAPL", 10, 100)), [], px(AAPL=110))
    close_to(r.day_return, D("0.01"))
    close_to(weights(r)["AAPL"], D(11) / D("1.01"))
    assert weights(r)["AAPL"].quantize(D("0.01")) == D("10.89")


def test_scenario_6_the_holding_limit_exits_at_exactly_five_sessions_and_keeps_at_four():
    b = book(
        hold("OLD", 5, 10, support=date(2026, 10, 2)),  # 5 sessions: 5, 6, 7, 8, 9
        hold("NEW", 5, 10, support=date(2026, 10, 5)),  # 4 sessions
    )
    r = run(b, [], px(OLD=10, NEW=10))
    assert set(r.book.holdings) == {"NEW"}
    assert r.exited_holding_limit == ("OLD",)


def test_a_buy_restarts_the_count_and_a_sell_does_not():
    stale = date(2026, 10, 2)
    r = run(
        book(hold("A", 5, 10, stale), hold("B", 5, 10, stale)),
        [report("A", "buy", 7), report("B", "sell", 3)],
        px(A=10, B=10),
    )
    assert weights(r) == {"A": D(7)}
    assert r.book.holdings["A"].support_session == DAY
    assert r.exited_holding_limit == ("B",)


def test_a_sell_leaves_the_support_session_alone():
    r = run(book(hold("B", 5, 10, date(2026, 10, 5))), [report("B", "sell", 3)], px(B=10))
    assert weights(r) == {"B": D(3)}
    assert r.book.holdings["B"].support_session == date(2026, 10, 5)


def test_a_new_agent_starts_at_100_with_a_day_return_of_zero():
    nb = new_book("research", DAY)
    assert (nb.index, nb.started_on, nb.holdings) == (100, DAY, {})
    assert run(nb, [], {}).day_return == 0


# --- report rules --------------------------------------------------------------------


def test_a_sell_above_the_current_weight_changes_nothing():
    r = run(book(hold("A", 5, 10)), [report("A", "sell", 9)], px(A=10))
    assert weights(r) == {"A": D(5)}


def test_a_sell_on_an_unheld_symbol_does_nothing():
    r = run(book(hold("A", 5, 10)), [report("Z", "sell", 0)], px(A=10, Z=3))
    assert set(r.book.holdings) == {"A"}
    assert r.exited_sell == ()


def test_hold_sets_the_weight_and_the_support_session():
    r = run(book(hold("A", 5, 10)), [report("A", "hold", 8)], px(A=10))
    assert weights(r) == {"A": D(8)}
    assert r.book.holdings["A"].support_session == DAY


def test_no_action_does_nothing():
    r = run(book(hold("A", 5, 10)), [report("", "no_action")], px(A=10))
    assert weights(r) == {"A": D(5)}


def test_reports_apply_in_order_a_buy_then_a_lower_sell_on_an_unheld_symbol():
    support = date(2026, 10, 8)
    r = run(
        new_book("research", DAY),
        [
            report("A", "buy", 5, support=support, minute=1),
            report("A", "sell", 2, support=DAY, minute=2),
        ],
        px(A=10),
    )
    assert weights(r) == {"A": D(2)}
    assert r.book.holdings["A"].support_session == support


def test_a_sell_then_a_buy_leaves_the_buys_weight():
    r = run(
        book(hold("A", 6, 10)),
        [report("A", "sell", 2, minute=1), report("A", "buy", 4, minute=2)],
        px(A=10),
    )
    assert weights(r) == {"A": D(4)}


def test_the_order_is_by_time_then_id_not_by_the_order_given():
    later = report("A", "buy", 9, minute=5)
    earlier = report("A", "buy", 3, minute=1)
    assert weights(run(new_book("research", DAY), [later, earlier], px(A=10))) == {"A": D(9)}


# --- missing prices ------------------------------------------------------------------


def test_an_unpriced_holding_returns_zero_and_drifts_with_the_rest():
    r = run(book(hold("A", 10, 100), hold("B", 10, 50)), [], px(A=110, B=None))
    close_to(r.day_return, D("0.01"))
    assert r.unpriced == ("B",)
    assert r.book.holdings["B"].ref_price == 50  # carried at its last price
    close_to(weights(r)["B"], D(10) / D("1.01"))


def test_an_unpriced_new_target_is_skipped_and_listed():
    r = run(new_book("research", DAY), [report("A"), report("B")], px(A=10, B=None))
    assert set(r.book.holdings) == {"A"}
    assert r.skipped_targets == ("B",)


def test_an_unpriced_target_already_held_keeps_its_last_price():
    r = run(book(hold("A", 5, 10)), [report("A", "buy", 8)], px(A=None))
    assert weights(r) == {"A": D(8)}
    assert r.book.holdings["A"].ref_price == 10
    assert r.skipped_targets == ()


def test_an_unpriced_exit_leaves_at_its_last_price():
    r = run(book(hold("A", 5, 10)), [report("A", "sell", 0)], px(A=None))
    assert r.book.holdings == {}
    assert r.exited_sell == ("A",)
    assert r.unpriced == ("A",)


def test_only_late_reports_that_were_applied_are_counted():
    r = run(
        new_book("research", DAY),
        [
            report("A", late=True, support=date(2026, 10, 8)),
            report("B"),
            report("C", "no_action", late=True),
        ],
        px(A=1, B=1),
    )
    assert r.late_reports == 1  # the late no_action report changed nothing


def test_a_late_buy_for_an_unpriced_new_symbol_is_skipped_and_not_counted():
    r = run(new_book("research", DAY), [report("A", late=True)], px(A=None))
    assert (r.late_reports, r.skipped_targets) == (0, ("A",))


def test_a_late_sell_counts_only_when_it_lowers_a_weight():
    held = book(hold("A", 5, 10), hold("B", 5, 10))
    lowers = run(held, [report("A", "sell", 2, late=True)], px(A=10, B=10))
    assert lowers.late_reports == 1
    same = run(held, [report("A", "sell", 9, late=True)], px(A=10, B=10))
    assert same.late_reports == 0
    unheld = run(held, [report("Z", "sell", 0, late=True)], px(A=10, B=10))
    assert unheld.late_reports == 0


def test_a_partial_sell_is_not_an_exit_and_a_full_sell_is():
    held = book(hold("A", 5, 10), hold("B", 5, 10))
    r = run(held, [report("A", "sell", 2), report("B", "sell", 0)], px(A=10, B=10))
    assert (r.exited_sell, weights(r)) == (("B",), {"A": D(2)})


def test_a_holding_whose_weight_is_zero_without_a_sell_is_not_called_a_sell():
    r = run(book(hold("A", 0, 10), hold("B", 5, 10)), [], px(A=10, B=10))
    assert r.exited_sell == ()
    assert set(r.book.holdings) == {"A", "B"}


def test_a_zero_weight_holding_still_ages_out_at_the_limit():
    old = date(2026, 10, 2)  # 5 sessions before DAY
    r = run(book(hold("A", 0, 10, old)), [], px(A=10))
    assert (r.exited_sell, r.exited_holding_limit) == ((), ("A",))


def test_a_sell_followed_by_a_buy_the_same_day_is_not_an_exit():
    r = run(
        book(hold("A", 5, 10)),
        [report("A", "sell", 0, minute=1), report("A", "buy", 4, minute=2)],
        px(A=10),
    )
    assert (r.exited_sell, weights(r)) == ((), {"A": D(4)})


# --- sessions_since ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "expected"),
    [
        (date(2026, 10, 9), 0),
        (date(2026, 10, 8), 1),
        (date(2026, 10, 5), 4),
        (date(2026, 10, 2), 5),
        (date(2026, 10, 3), 5),
        (date(2026, 9, 1), 5),
    ],
)
def test_sessions_since_counts_to_the_limit(a, expected):
    assert sessions_since(a, DAY, previous_session, 5) == expected


def test_sessions_since_works_with_a_stub_calendar():
    assert (
        sessions_since(
            date(2026, 1, 1), date(2026, 1, 4), lambda d: date.fromordinal(d.toordinal() - 1), 10
        )
        == 3
    )


# --- a three-session fixture, worked by hand -------------------------------------------
# Session 1, Mon 10-05: new book. Buy A 50% at 100. Index 100, A weight 50.
# Session 2, Tue 10-06: A closes 110 (r = +0.10). R = 50 * 0.10 / 100 = 0.05, index 105.
#   A drifts to 50 * 1.10 / 1.05 = 52.380952...; buy B 20% at 40. Weights: A 52.38, B 20.
# Session 3, Wed 10-07: A closes 99 (r = 99/110 - 1 = -0.10), B closes 44 (r = +0.10).
#   R = (52.380952 * -0.10 + 20 * 0.10) / 100 = (-5.238095 + 2) / 100.
#   index = 105 * (1 + R) = 105 - 5.5 + 2.1 = 101.6 exactly.
#   A drifts to 55/1.05 * 0.9 * 105 / 101.6 = 4950 / 101.6 = 48.720472...
#   B drifts to 20 * 1.1 * 105 / 101.6 = 2310 / 101.6 = 22.736220...


def test_three_sessions_match_the_hand_computation():
    d1, d2, d3 = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)
    r1 = run(new_book("research", d1), [report("A", size=50, support=d1)], px(A=100), day=d1)
    assert (r1.book.index, weights(r1)) == (100, {"A": D(50)})
    r2 = run(r1.book, [report("B", size=20, support=d2)], px(A=110, B=40), day=d2)
    close_to(r2.day_return, D("0.05"))
    close_to(r2.book.index, D(105))
    close_to(weights(r2)["A"], D(55) / D("1.05"))
    assert weights(r2)["B"] == 20
    r3 = run(r2.book, [], px(A=99, B=44), day=d3)
    close_to(r3.book.index, D("101.6"), D("1e-18"))
    close_to(r3.day_return, D("101.6") / 105 - 1, D("1e-18"))
    close_to(weights(r3)["A"], D(4950) / D("101.6"), D("1e-18"))
    close_to(weights(r3)["B"], D(2310) / D("101.6"), D("1e-18"))
    assert r3.book.started_on == d1


# --- properties ----------------------------------------------------------------------

SESSIONS = [
    date(2026, 10, 5),
    date(2026, 10, 6),
    date(2026, 10, 7),
    date(2026, 10, 8),
    date(2026, 10, 9),
    date(2026, 10, 12),
    date(2026, 10, 13),
    date(2026, 10, 14),
]
SYMBOLS = ["A", "B", "C", "D"]
_size = st.integers(min_value=0, max_value=100).map(lambda n: D(n) / 4)
_price = st.integers(min_value=1, max_value=100_000).map(lambda n: D(n) / 100)
_report = st.tuples(
    st.sampled_from(SYMBOLS),
    st.sampled_from(["buy", "sell", "hold", "no_action"]),
    _size,
    st.integers(min_value=0, max_value=59),
)
_day = st.tuples(
    st.lists(_report, max_size=6),
    st.fixed_dictionaries({s: st.one_of(st.none(), _price) for s in SYMBOLS}),
)


def _rows(day, raw):
    rows = []
    for symbol, direction, size, minute in raw:
        if direction == "no_action":
            symbol, size = "", None
        elif direction != "sell":
            size = max(size, D("0.25"))
        rows.append(
            ReportRow(
                next(_ids),
                "research",
                datetime(day.year, day.month, day.day, 10, minute, tzinfo=UTC),
                symbol,
                direction,
                size,
                day,
                False,
            )
        )
    return rows


@settings(max_examples=150, deadline=None)
@given(st.lists(_day, min_size=1, max_size=len(SESSIONS)))
def test_properties_hold_across_sessions_with_storage_in_between(days):
    current = new_book("research", SESSIONS[0])
    for day, (raw, closes) in zip(SESSIONS, days, strict=False):
        prices = {s: Price(s, c, None if c else "no_price") for s, c in closes.items()}
        result = advance(current, _rows(day, raw), prices, day, LIMIT, previous_session)
        assert 1 + result.day_return > 0
        assert result.book.index >= 0
        assert all(h.weight_pct >= 0 for h in result.book.holdings.values())
        close_to(
            min(sum(w for w in weights(result).values()), D(100)),
            sum(weights(result).values()),
            D("1e-20"),
        )
        stored = encode(
            day=day,
            previous_day=None,
            sessions_covered=1,
            missed_sessions=(),
            holding_sessions=LIMIT,
            summary_version="0.1",
            account_return=None,
            account_base="open",
            close_taken_at=None,
            results={"research": result},
            usage={},
        )
        current = decode_books(stored)["research"]
        total = sum((h.weight_pct for h in current.holdings.values()), D(0))
        assert total <= 100  # the rounded-down weights never sum over 100
        assert all(h.weight_pct >= 0 for h in current.holdings.values())
