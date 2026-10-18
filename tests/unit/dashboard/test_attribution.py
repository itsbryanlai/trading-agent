"""Attribution parsing, comparison series and usage totals (specs/014 tasks T023; US5 4, 5)."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.unit.dashboard import attribution_fixtures as fx
from trading_agent.dashboard import attribution as at
from trading_agent.dashboard.attribution import Attribution, Unreadable


def D(text: str) -> date:
    return date.fromisoformat(text)


def parsed(obj):
    result = at.parse(obj)
    assert isinstance(result, Attribution), result
    return result


# --- parse ----------------------------------------------------------------------------------


def test_the_contract_example_parses_with_decimals_from_strings():
    row = parsed(fx.CONTRACT_EXAMPLE)
    assert row.trading_day == D("2026-10-09") and row.previous_trading_day == D("2026-10-08")
    assert row.sessions_covered == 1 and row.missed_sessions == ()
    assert row.account_return == Decimal("0.000000") and row.account_base == "previous_close"
    assert row.close_taken_at == datetime(2026, 10, 9, 19, 30, 4, tzinfo=UTC)
    book = row.agents["research"]
    assert book.started_on == D("2026-10-09") and book.index == Decimal("100.00000000")
    assert type(book.index) is Decimal and book.day_return == Decimal("0")
    (holding,) = book.holdings
    assert (holding.symbol, holding.weight_pct, holding.ref_price) == (
        "AAPL",
        Decimal("5.000000"),
        Decimal("229.1500"),
    )
    assert holding.support_session == D("2026-10-09")
    assert book.scaled_by is None and book.late_reports == 0
    assert book.unpriced == () and book.skipped_targets == ()
    assert book.exited_sell == () and book.exited_holding_limit == ()
    assert book.usage == {
        "written": 3, "argued": 2, "no_action": 1, "cited": 1,
        "cited_decisions": 1, "approved": 1, "filled": 0,
    }  # fmt: skip


def test_missed_sessions_unpriced_and_exits_are_read():
    obj = fx.example(missed_sessions=["2026-10-07", "2026-10-08"], sessions_covered=3)
    book = obj["agents"]["research"]
    book.update(unpriced=["MU"], skipped_targets=["NVDA"], scaled_by="0.800000")
    book["exited"] = {"sell": ["TSLA"], "holding_limit": ["AMD"]}
    row = parsed(obj)
    assert row.missed_sessions == (D("2026-10-07"), D("2026-10-08")) and row.sessions_covered == 3
    got = row.agents["research"]
    assert got.unpriced == ("MU",) and got.skipped_targets == ("NVDA",)
    assert got.exited_sell == ("TSLA",) and got.exited_holding_limit == ("AMD",)
    assert got.scaled_by == Decimal("0.800000")


def test_unknown_keys_are_ignored_at_every_level():
    obj = fx.example(future_field={"a": 1})
    obj["account"]["extra"] = 1
    obj["agents"]["research"]["extra"] = [1]
    obj["agents"]["research"]["holdings"]["AAPL"]["extra"] = "x"
    assert isinstance(at.parse(obj), Attribution)


def test_the_first_ever_row_has_a_null_previous_day_and_a_null_return():
    obj = fx.example(previous_trading_day=None)
    obj["account"]["return"] = None
    row = parsed(obj)
    assert row.previous_trading_day is None and row.account_return is None


@pytest.mark.parametrize("version", [2, 0, "1", 1.0, True, None])
def test_another_schema_version_is_unreadable(version):
    result = at.parse(fx.example(schema_version=version))
    assert result == Unreadable("unknown schema version")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.pop("schema_version"),
        lambda o: o.pop("trading_day"),
        lambda o: o.pop("account"),
        lambda o: o["account"].pop("return"),
        lambda o: o.pop("agents"),
        lambda o: o["agents"]["research"].pop("started_on"),
        lambda o: o["agents"]["research"].pop("index"),
        lambda o: o["agents"]["research"].pop("day_return"),
    ],
)
def test_a_missing_required_key_is_unreadable(mutate):
    obj = copy.deepcopy(fx.CONTRACT_EXAMPLE)
    mutate(obj)
    assert isinstance(at.parse(obj), Unreadable)


@pytest.mark.parametrize(
    "bad", [0.5, 1, True, "abc", "1e3", "NaN", "Infinity", "1,5", "", " 1", [], {}]
)
@pytest.mark.parametrize(
    "where",
    ["index", "day_return", "weight_pct", "ref_price", "scaled_by_set", "account_return"],
)
def test_a_number_that_is_not_a_decimal_string_makes_the_row_unreadable(where, bad):
    obj = copy.deepcopy(fx.CONTRACT_EXAMPLE)
    book = obj["agents"]["research"]
    if where == "account_return":
        obj["account"]["return"] = bad
    elif where == "scaled_by_set":
        book["scaled_by"] = bad
    elif where in {"weight_pct", "ref_price"}:
        book["holdings"]["AAPL"][where] = bad
    else:
        book[where] = bad
    result = at.parse(obj)
    assert isinstance(result, Unreadable)
    assert "abc" not in result.reason and "NaN" not in result.reason  # never the value


@pytest.mark.parametrize(
    "bad", ["2026-13-01", "2026-10-9", "20261009", "Oct 9", 20261009, None, ""]
)
def test_a_bad_date_is_unreadable(bad):
    assert isinstance(at.parse(fx.example(trading_day=bad)), Unreadable)
    obj = fx.example()
    obj["agents"]["research"]["started_on"] = bad
    assert isinstance(at.parse(obj), Unreadable)


def test_bad_shapes_are_unreadable_not_errors():
    for obj in (None, 5, "x", [], [fx.CONTRACT_EXAMPLE]):
        assert isinstance(at.parse(obj), Unreadable)
    for patch in (
        {"agents": []},
        {"account": "x"},
        {"missed_sessions": "2026-10-08"},
        {"missed_sessions": [5]},
    ):
        assert isinstance(at.parse(fx.example(**patch)), Unreadable)
    obj = fx.example()
    obj["agents"]["research"]["holdings"] = []
    obj["agents"]["research"]["unpriced"] = "MU"
    assert isinstance(at.parse(obj), Unreadable)


def test_a_naive_close_time_is_unreadable():
    obj = fx.example()
    obj["account"]["close_taken_at"] = "2026-10-09T19:30:04"
    assert isinstance(at.parse(obj), Unreadable)


def test_hostile_symbols_and_agent_names_are_kept_as_plain_text():
    obj = fx.example()
    obj["agents"]["<script>x</script>"] = copy.deepcopy(obj["agents"]["research"])
    obj["agents"]["research"]["holdings"]['"><img src=x onerror=1>'] = {
        "weight_pct": "1", "ref_price": "1", "support_session": None,
    }  # fmt: skip
    row = parsed(obj)
    assert "<script>x</script>" in row.agents
    assert any(h.symbol.startswith('"><img') for h in row.agents["research"].holdings)


def test_a_malformed_usage_flags_the_usage_not_the_row():
    good = fx.CONTRACT_EXAMPLE["agents"]["research"]["usage"]
    for usage in (
        {"written": 1},
        {**good, "filled": "0"},
        {**good, "cited": 1.5},
        {**good, "argued": True},
        {**good, "written": -1},
        "x",
        None,
    ):
        obj = fx.example()
        obj["agents"]["research"]["usage"] = usage
        assert parsed(obj).agents["research"].usage is None
    obj = fx.example()
    del obj["agents"]["research"]["usage"]
    assert parsed(obj).agents["research"].usage is None


# --- comparison -----------------------------------------------------------------------------


def rows_of(*pairs):
    return [(D(d), at.parse(obj)) for d, obj in pairs]


def values(series):
    return [None if v is None else float(v) for _, v in series]


def test_the_account_compounds_its_returns_from_100():
    rows = rows_of(
        ("2026-10-05", fx.day("2026-10-05", "0.020000")),  # unseen: the first point is the base
        ("2026-10-06", fx.day("2026-10-06", "0.010000")),
        ("2026-10-07", fx.day("2026-10-07", "-0.050000")),
        ("2026-10-08", fx.day("2026-10-08", "0.000000")),
    )
    got = at.comparison(rows)["account"]
    assert [d for d, _ in got] == [
        D("2026-10-05"),
        D("2026-10-06"),
        D("2026-10-07"),
        D("2026-10-08"),
    ]
    assert values(got) == pytest.approx([100.0, 101.0, 95.95, 95.95])
    assert all(type(v) is Decimal for _, v in got)
    assert got[2][1] == Decimal("100") * Decimal("1.010000") * Decimal("0.950000")


def test_a_null_return_or_an_unreadable_row_is_a_gap_and_the_next_point_restarts():
    rows = rows_of(
        ("2026-10-05", fx.day("2026-10-05", "0.010000")),
        ("2026-10-06", fx.day("2026-10-06", "0.100000")),
        ("2026-10-07", fx.day("2026-10-07", None)),
        ("2026-10-08", fx.day("2026-10-08", "0.200000")),
        ("2026-10-09", fx.example(schema_version=2)),
        ("2026-10-12", fx.day("2026-10-12", "0.010000")),
        ("2026-10-13", fx.day("2026-10-13", "0.010000")),
    )
    got = at.comparison(rows)["account"]
    assert values(got) == pytest.approx([100.0, 110.0, None, 100.0, None, 100.0, 101.0])


def test_rows_are_ordered_by_day_whatever_order_they_arrive_in():
    rows = rows_of(
        ("2026-10-06", fx.day("2026-10-06", "0.100000")),
        ("2026-10-05", fx.day("2026-10-05", "0.010000")),
    )
    assert values(at.comparison(rows)["account"]) == pytest.approx([100.0, 110.0])


def test_each_analyst_series_is_its_stored_index_from_its_started_on_day():
    early = fx.book("2026-10-05", "100.00000000")
    rows = rows_of(
        ("2026-10-05", fx.day("2026-10-05", "0.01", agents={"research": early})),
        (
            "2026-10-06",
            fx.day(
                "2026-10-06", "0.01", agents={"research": fx.book("2026-10-05", "104.50000000")}
            ),
        ),
        (
            "2026-10-07",
            fx.day(
                "2026-10-07",
                "0.01",
                agents={
                    "research": fx.book("2026-10-05", "103.00000000"),
                    "opportunistic_identifier": fx.book("2026-10-07", "100.00000000"),
                },
            ),
        ),
    )
    series = at.comparison(rows)
    assert set(series) == {"account", "research", "opportunistic_identifier"}
    assert values(series["research"]) == [100.0, 104.5, 103.0]
    # An analyst who starts mid-range has no point before its started_on day.
    assert values(series["opportunistic_identifier"]) == [None, None, 100.0]


def test_an_analyst_is_a_gap_where_its_row_is_unreadable_or_does_not_list_it():
    rows = rows_of(
        (
            "2026-10-05",
            fx.day("2026-10-05", "0.01", agents={"research": fx.book("2026-10-05", "100")}),
        ),
        ("2026-10-06", fx.example(schema_version=9)),
        (
            "2026-10-07",
            fx.day("2026-10-07", "0.01", agents={"research": fx.book("2026-10-05", "102")}),
        ),
    )
    assert values(at.comparison(rows)["research"]) == [100.0, None, 102.0]


def test_a_row_dated_before_started_on_gives_no_point():
    rows = rows_of(
        (
            "2026-10-05",
            fx.day("2026-10-05", "0.01", agents={"research": fx.book("2026-10-09", "100")}),
        )
    )
    assert values(at.comparison(rows)["research"]) == [None]


def test_no_rows_means_only_an_empty_account_series():
    assert at.comparison([]) == {"account": []}


# --- usage totals ---------------------------------------------------------------------------


def _agents(**books):
    return books


def test_usage_totals_are_the_sums_of_each_rows_counts():
    zeros = dict.fromkeys(at.USAGE_FIELDS, 0)
    first = fx.book("2026-10-05", "100", written=3, filled=1)
    second = fx.book("2026-10-05", "100", written=4, argued=5, no_action=2)
    third = fx.book("2026-10-05", "100", **zeros)
    other = fx.book("2026-10-07", "100", written=7)
    rows = rows_of(
        ("2026-10-05", fx.day("2026-10-05", "0.01", agents={"research": first})),
        ("2026-10-06", fx.day("2026-10-06", "0.01", agents={"research": second})),
        (
            "2026-10-07",
            fx.day(
                "2026-10-07",
                "0.01",
                agents={"research": third, "opportunistic_identifier": other},
            ),
        ),
    )
    result = at.usage_totals(rows)
    assert result.flagged == ()
    assert result.totals["research"] == {
        "written": 7,
        "argued": 7,
        "no_action": 3,
        "cited": 2,
        "cited_decisions": 2,
        "approved": 2,
        "filled": 1,
    }
    assert result.totals["opportunistic_identifier"]["written"] == 7
    assert list(result.totals["research"]) == list(at.USAGE_FIELDS)


def test_a_malformed_usage_is_skipped_and_flagged_without_failing():
    bad = fx.book("2026-10-05", "100")
    bad["usage"]["filled"] = "0"
    rows = rows_of(
        (
            "2026-10-05",
            fx.day(
                "2026-10-05", "0.01", agents={"research": fx.book("2026-10-05", "100", written=3)}
            ),
        ),
        ("2026-10-06", fx.day("2026-10-06", "0.01", agents={"research": bad})),
        ("2026-10-07", fx.example(schema_version=3)),
    )
    result = at.usage_totals(rows)
    assert result.totals["research"]["written"] == 3  # only the readable row counted
    assert result.flagged == ((D("2026-10-06"), "research"), (D("2026-10-07"), None))


def test_no_rows_no_totals():
    result = at.usage_totals([])
    assert result.totals == {} and result.flagged == ()
