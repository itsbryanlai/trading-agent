"""Order-history view models (specs/014 tasks T021; FR-008, US3 scenarios 1-4)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.unit.dashboard import order_rows as rows
from trading_agent.dashboard import pages_orders as po

SGT = ZoneInfo("Asia/Singapore")


def one(raw) -> po.OrderRow:
    return po.order_row(raw, SGT)


def lines(row: po.OrderRow) -> list[str]:
    return [t.full for t in row.outcome]


# --- the four cases and the in-flight one ---------------------------------------------------


def test_a_rejected_decision_shows_the_decision_the_rule_and_no_order():
    row = one(rows.rejected())
    assert row.cause_kind == "decision" and row.cause == "buy AAPL, target 5%"
    assert "Decided 19 Oct 22:00 SGT (19 Oct 10:00 ET); quote 187.25" in row.cause_detail
    assert (row.verdict, row.rule, row.approved) == ("rejected", "trading_paused", None)
    assert (row.outcome_kind, lines(row)) == ("none", ["No order."])


def test_a_filled_order_shows_status_limit_and_fill():
    row = one(rows.filled())
    assert row.verdict == "approved" and row.rule is None
    assert row.approved == "buy 10 AAPL, limit 187.25"
    assert row.outcome_kind == "order"
    assert lines(row) == [
        "Order filled, submitted 19 Oct 22:00 SGT (19 Oct 10:00 ET)",
        "Limit price 187.25",
        "Filled 10 at 187.10",
    ]


def test_a_rejected_order_shows_the_brokers_reason():
    row = one(rows.filled(status="rejected", fill_qty=None, fill_price=None, broker_reason="no"))
    assert "Broker reason: no" in lines(row) and "Order rejected" in lines(row)[0]
    assert not any(line.startswith("Filled") for line in lines(row))


def test_a_sell_order_has_no_limit_line():
    row = one(rows.filled(limit_price=None))
    assert not any(line.startswith("Limit") for line in lines(row))


def test_a_refusal_shows_its_named_reason_and_details_instead_of_an_order():
    row = one(rows.refused())
    assert row.outcome_kind == "refusal"
    assert lines(row) == [
        "Refused by Execution: quote_above_ceiling, 19 Oct 22:00 SGT (19 Oct 10:00 ET)",
        "ceiling: 190.00",
        "quote: 191.5",
    ]


def test_refusal_details_that_are_not_text_are_shown_as_json_text():
    row = one(rows.refused(refusal_details={"nested": {"a": 1}, "n": 3, "z": None}))
    assert lines(row)[1:] == ['nested: {"a": 1}', "n: 3", "z: null"]
    assert one(rows.refused(refusal_details={}))  # an empty object adds no lines
    assert len(lines(one(rows.refused(refusal_details={})))) == 1
    assert lines(one(rows.refused(refusal_details=[1, 2])))[1:] == ["[1, 2]"]
    assert len(lines(one(rows.refused(refusal_details=None)))) == 1


def test_a_stop_loss_exit_shows_the_trigger_in_place_of_a_decision():
    row = one(rows.stop_loss())
    assert row.cause_kind == "stop_loss" and row.symbol == "MU"
    assert row.cause == "stop-loss exit MU"
    assert row.cause_detail == "Price seen 160.00 at 19 Oct 22:00 SGT (19 Oct 10:00 ET)"
    assert row.outcome_kind == "awaiting"


def test_an_approved_verdict_with_no_outcome_yet_is_awaiting_execution():
    row = one(rows.in_flight())
    assert (row.outcome_kind, lines(row)) == ("awaiting", ["Awaiting Execution."])


def test_a_verdict_with_both_an_order_and_a_refusal_shows_both_without_failing():
    both = rows.filled(
        refusal_reason="trading_paused", refusal_details={"a": "b"}, refused_at=rows.AT
    )
    row = one(both)
    assert row.outcome_kind == "order_and_refusal"
    assert any(line.startswith("Order") for line in lines(row))
    assert any(line.startswith("Refused") for line in lines(row))


def test_stored_text_is_returned_as_plain_text_never_markup():
    hostile = '<script>alert(1)</script> "x" {{ 7*7 }}'
    row = one(rows.filled(status="rejected", broker_reason=hostile))
    assert hostile in lines(row)[-1]
    assert type(lines(row)[-1]) is str
    refused = one(rows.refused(refusal_details={"<b>k</b>": hostile}))
    assert type(lines(refused)[1]) is str


def test_long_text_carries_a_short_summary_and_short_text_does_not():
    long = "x" * 1000
    row = one(rows.filled(status="rejected", broker_reason=long))
    note = row.outcome[-1]
    assert note.full.endswith(long) and note.summary is not None and len(note.summary) <= 301
    assert row.outcome[0].summary is None


# --- filters (contracts/routes.md query validation) -----------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("aapl", "AAPL"),
        (" brk.b ", "BRK.B"),
        ("BF-B", "BF-B"),
        ("A" * 10, "A" * 10),
        ("A" * 11, None),
        ("AAP1", None),
        ("AA PL", None),
        ("'; DROP TABLE x;--", None),
        ("", None),
        (None, None),
    ],
)
def test_symbol_validation(raw, expected):
    assert po.parse_filters(raw, None, None, None).symbol == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-19", date(2026, 10, 19)),
        (" 2026-10-19 ", date(2026, 10, 19)),
        ("2026-02-30", None),
        ("20261019", None),
        ("2026-W42-1", None),
        ("19/10/2026", None),
        ("", None),
        (None, None),
    ],
)
def test_date_validation(raw, expected):
    parsed = po.parse_filters(None, raw, raw, None)
    assert parsed.day_from == expected and parsed.day_to == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", 1), ("7", 7), ("0", 1), ("-3", 1), ("abc", 1), ("2.5", 1), ("", 1), (None, 1),
     ("9999999", 1), (" 4 ", 4)],
)  # fmt: skip
def test_page_validation(raw, expected):
    assert po.parse_filters(None, None, None, raw).page == expected


def test_dates_become_owner_zone_day_starts_in_utc_with_the_end_exclusive():
    filters = po.parse_filters(None, "2026-10-19", "2026-10-20", None)
    since, until = po.bounds(filters, SGT)
    assert since == datetime(2026, 10, 18, 16, 0, tzinfo=UTC)  # 19 Oct 00:00 +08
    assert until == datetime(2026, 10, 20, 16, 0, tzinfo=UTC)  # the day after the 20th
    assert po.bounds(po.Filters(), SGT) == (None, None)
    assert po.bounds(po.Filters(day_to=date.max), SGT) == (None, None)


@given(st.text(max_size=30), st.text(max_size=30), st.text(max_size=30), st.text(max_size=10))
def test_no_input_makes_parse_or_bounds_fail(symbol, day_from, day_to, page):
    filters = po.parse_filters(symbol, day_from, day_to, page)
    po.bounds(filters, SGT)
    assert filters.page >= 1


# --- paging ---------------------------------------------------------------------------------


def test_the_extra_row_only_says_there_is_a_next_page():
    many = [rows.rejected(id=f"v-{i}") for i in range(51)]
    view = po.orders_view(many, po.Filters(page=1), 50, SGT)
    assert len(view.rows) == 50 and view.next_url == "/orders?page=2" and view.prev_url is None
    exact = po.orders_view(many[:50], po.Filters(page=1), 50, SGT)
    assert exact.next_url is None


def test_links_keep_the_filters_and_page_one_has_no_page_parameter():
    filters = po.Filters(
        symbol="AAPL", day_from=date(2026, 10, 1), day_to=date(2026, 10, 9), page=2
    )
    view = po.orders_view([rows.rejected()] * 51, filters, 50, SGT)
    assert view.prev_url == "/orders?symbol=AAPL&from=2026-10-01&to=2026-10-09"
    assert view.next_url == "/orders?symbol=AAPL&from=2026-10-01&to=2026-10-09&page=3"
    assert (view.symbol_value, view.from_value, view.to_value) == (
        "AAPL",
        "2026-10-01",
        "2026-10-09",
    )


def test_an_empty_list_and_a_page_past_the_end_say_so():
    view = po.orders_view([], po.Filters(symbol="ZZZ", page=4), 50, SGT)
    assert view.rows == () and view.empty_text == "No orders match."
    assert view.prev_url == "/orders?symbol=ZZZ&page=3" and view.next_url is None
