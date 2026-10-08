"""Encoding and decoding the row's attribution object (research J11, contracts/attribution.md)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.journal.model import Book, BookResult, Holding
from trading_agent.journal.state import MISSED_LISTED, UnknownSchema, decode_books, encode

DAY = date(2026, 10, 9)
USAGE = {
    "written": 3,
    "argued": 2,
    "no_action": 1,
    "cited": 1,
    "cited_decisions": 1,
    "approved": 1,
    "filled": 0,
}


def _result(book: Book, **over) -> BookResult:
    fields = dict(
        book=book,
        day_return=Decimal("0"),
        scaled_by=None,
        late_reports=0,
        unpriced=(),
        skipped_targets=(),
        exited_sell=(),
        exited_holding_limit=(),
    )
    return BookResult(**{**fields, **over})


def _encode(results, usage=None, **over):
    args = dict(
        day=DAY,
        previous_day=date(2026, 10, 8),
        sessions_covered=1,
        missed_sessions=(),
        holding_sessions=5,
        summary_version="0.1",
        account_return=Decimal("0"),
        account_base="previous_close",
        close_taken_at=datetime(2026, 10, 9, 19, 30, 4, tzinfo=UTC),
        results=results,
        usage=usage or {agent: USAGE for agent in results},
    )
    return encode(**{**args, **over})


def _book(agent="research", **holdings) -> Book:
    return Book(
        agent,
        date(2026, 10, 9),
        Decimal("100"),
        {s: Holding(s, Decimal(w), Decimal(p), DAY) for s, (w, p) in holdings.items()},
    )


EXAMPLE = {
    "schema_version": 1,
    "trading_day": "2026-10-09",
    "previous_trading_day": "2026-10-08",
    "sessions_covered": 1,
    "missed_sessions": [],
    "holding_sessions": 5,
    "summary_version": "0.1",
    "account": {
        "return": "0.000000",
        "base": "previous_close",
        "close_taken_at": "2026-10-09T19:30:04+00:00",
    },
    "agents": {
        "research": {
            "started_on": "2026-10-09",
            "index": "100.00000000",
            "day_return": "0.000000",
            "holdings": {
                "AAPL": {
                    "weight_pct": "5.000000",
                    "ref_price": "229.1500",
                    "support_session": "2026-10-09",
                }
            },
            "scaled_by": None,
            "late_reports": 0,
            "unpriced": [],
            "skipped_targets": [],
            "exited": {"sell": [], "holding_limit": []},
            "usage": USAGE,
        }
    },
}


def test_the_documented_example_is_produced_exactly():
    book = _book(AAPL=("5", "229.1500"))
    assert _encode({"research": _result(book)}) == EXAMPLE


def test_the_documented_example_decodes():
    books = decode_books(EXAMPLE)
    assert set(books) == {"research"}
    book = books["research"]
    assert book.started_on == DAY
    assert book.index == Decimal("100")
    assert book.holdings["AAPL"] == Holding("AAPL", Decimal("5"), Decimal("229.15"), DAY)


def test_the_object_is_json_serialisable_and_numbers_are_strings():
    attribution = _encode({"research": _result(_book(AAPL=("5", "229.15")))})
    text = json.dumps(attribution)
    assert json.loads(text) == attribution
    agent = attribution["agents"]["research"]
    assert isinstance(agent["index"], str)
    assert isinstance(agent["holdings"]["AAPL"]["weight_pct"], str)


def test_rounding_per_the_conventions():
    book = Book(
        "research",
        DAY,
        Decimal("100.123456785"),
        {"AAPL": Holding("AAPL", Decimal("33.3333339"), Decimal("10.5"), DAY)},
    )
    result = _result(book, day_return=Decimal("0.0123455"), scaled_by=Decimal("0.987654321"))
    whole = _encode({"research": result}, account_return=Decimal("-0.0000005"))
    assert whole["account"]["return"] == "0.000000"  # never "-0.000000"
    out = whole["agents"]["research"]
    assert out["holdings"]["AAPL"]["weight_pct"] == "33.333333"  # rounded down
    assert out["index"] == "100.12345678"  # half-even
    assert out["day_return"] == "0.012346"  # 0.0123455 -> half-even to ...46
    assert out["scaled_by"] == "0.98765432"


def test_account_return_is_rounded_and_may_be_null():
    book = _book()
    got = _encode({"research": _result(book)}, account_return=Decimal("0.0123456789"))
    assert got["account"]["return"] == "0.012346"
    got = _encode({"research": _result(book)}, account_return=None, close_taken_at=None)
    assert got["account"]["return"] is None
    assert got["account"]["close_taken_at"] is None


def test_the_weights_round_down_so_they_never_sum_over_100():
    third = Decimal(100) / Decimal(3)
    holdings = {s: Holding(s, third, Decimal("1"), DAY) for s in ("A", "B", "C")}
    book = Book("research", DAY, Decimal("100"), holdings)
    out = _encode({"research": _result(book)})["agents"]["research"]["holdings"]
    assert sum(Decimal(h["weight_pct"]) for h in out.values()) <= 100


def test_symbols_and_agents_are_sorted():
    results = {
        "research": _result(_book("research", MSFT=("1", "1"), AAPL=("1", "1"))),
        "opportunistic_identifier": _result(_book("opportunistic_identifier")),
    }
    out = _encode(results)
    assert list(out["agents"]) == ["opportunistic_identifier", "research"]
    assert list(out["agents"]["research"]["holdings"]) == ["AAPL", "MSFT"]


def test_lists_in_the_result_are_sorted():
    result = _result(
        _book(),
        unpriced=("ZZZ", "AAA"),
        skipped_targets=("MMM", "BBB"),
        exited_sell=("Y", "X"),
        exited_holding_limit=("Q", "P"),
    )
    out = _encode({"research": result})["agents"]["research"]
    assert out["unpriced"] == ["AAA", "ZZZ"]
    assert out["skipped_targets"] == ["BBB", "MMM"]
    assert out["exited"] == {"sell": ["X", "Y"], "holding_limit": ["P", "Q"]}


def test_missed_sessions_are_capped_at_thirty():
    missed = tuple(date.fromordinal(date(2026, 1, 1).toordinal() + i) for i in range(45))
    out = _encode({"research": _result(_book())}, missed_sessions=missed)
    assert MISSED_LISTED == 30
    assert out["missed_sessions"] == [d.isoformat() for d in missed[:30]]


def test_the_first_ever_run_has_no_previous_day():
    out = _encode({"research": _result(_book())}, previous_day=None, account_base="open")
    assert out["previous_trading_day"] is None
    assert out["account"]["base"] == "open"


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_an_unknown_schema_version_is_refused(version):
    with pytest.raises(UnknownSchema):
        decode_books({**EXAMPLE, "schema_version": version})


def test_a_missing_version_or_a_malformed_object_is_refused():
    without = {k: v for k, v in EXAMPLE.items() if k != "schema_version"}
    with pytest.raises(UnknownSchema):
        decode_books(without)
    broken = {**EXAMPLE, "agents": {"research": {"index": "not a number"}}}
    with pytest.raises(UnknownSchema):
        decode_books(broken)
    with pytest.raises(UnknownSchema):
        decode_books("nonsense")  # type: ignore[arg-type]


_symbols = st.sampled_from(["AAPL", "MSFT", "NVDA", "BRK.B", "X"])
_weight = st.decimals(min_value=0, max_value=100, places=9, allow_nan=False)
_price = st.decimals(min_value=Decimal("0.0001"), max_value=1_000_000, places=6)
_holdings = st.dictionaries(
    _symbols,
    st.builds(
        lambda w, p, d: (w, p, d),
        _weight,
        _price,
        st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 1, 1)),
    ),
    max_size=5,
)
_index = st.decimals(min_value=0, max_value=1_000_000, places=12)


@given(
    _holdings,
    _index,
    st.decimals(min_value=-1, max_value=10, places=10),
    st.one_of(st.none(), st.decimals(min_value=Decimal("0.01"), max_value=1, places=10)),
)
def test_a_decoded_book_re_encodes_identically(holdings, index, day_return, scaled_by):
    # What makes a re-run identical (SC-004): the next run reads what this one stored.
    book = Book(
        "research",
        DAY,
        index,
        {s: Holding(s, w, p, d) for s, (w, p, d) in holdings.items()},
    )
    first = _encode({"research": _result(book, day_return=day_return, scaled_by=scaled_by)})
    stored = first["agents"]["research"]
    decoded = decode_books(first)["research"]
    again = _encode(
        {
            "research": _result(
                decoded,
                day_return=Decimal(stored["day_return"]),
                scaled_by=None if scaled_by is None else Decimal(stored["scaled_by"]),
            )
        }
    )
    assert again == first
