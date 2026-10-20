"""The /orders page (specs/014 tasks T021; US3 scenarios 1-5)."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.fakes.dashboard_store import FakeStore, failing
from tests.unit.dashboard import order_rows as rows
from tests.unit.dashboard.helpers import Built, build

HOSTILE = "<img src=x onerror=alert(1)>"


def get(built: Built, query: str = ""):
    _, cookie = built.sign_in()
    return built.client().get("/orders" + query, headers=cookie)


def with_rows(*items, **kwargs) -> Built:
    return build(store=FakeStore(order_rows=list(items), **kwargs))


def test_scenario_rejected_decision_has_the_rule_and_no_order():
    text = get(with_rows(rows.rejected())).text
    assert "buy AAPL, target 5%" in text and "rejected" in text and "trading_paused" in text
    assert "No order." in text


def test_scenario_order_shows_status_limit_fill_and_broker_reason():
    text = get(with_rows(rows.filled(status="rejected", broker_reason="insufficient"))).text
    assert "Order rejected" in text and "Limit price 187.25" in text
    assert "Broker reason: insufficient" in text


def test_scenario_refusal_shows_its_reason():
    text = get(with_rows(rows.refused())).text
    assert "Refused by Execution: quote_above_ceiling" in text and "ceiling: 190.00" in text


def test_scenario_stop_loss_shows_the_trigger_price_and_time():
    text = get(with_rows(rows.stop_loss())).text
    assert "stop-loss exit MU" in text and "Price seen 160.00 at" in text


def test_an_in_flight_verdict_is_awaiting_execution():
    assert "Awaiting Execution." in get(with_rows(rows.in_flight())).text


def test_each_verdict_appears_once_newest_first_in_store_order():
    items = [rows.rejected(id=f"v-{i}") for i in range(3)]
    text = get(with_rows(*items)).text
    assert [text.count(f'id="verdict-v-{i}"') for i in range(3)] == [1, 1, 1]
    assert text.index("verdict-v-0") < text.index("verdict-v-1") < text.index("verdict-v-2")


def test_stored_text_is_escaped_never_live_html():
    items = [
        rows.filled(status="rejected", broker_reason=HOSTILE),
        rows.refused(refusal_details={"k": HOSTILE, HOSTILE: "v"}),
        rows.rejected(rejection_rule=HOSTILE),
    ]
    text = get(with_rows(*items)).text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text
    assert "<img" not in text


def test_long_broker_text_is_collapsed_with_the_rest_on_the_same_page():
    text = get(with_rows(rows.filled(status="rejected", broker_reason="y" * 5000))).text
    assert "<details><summary>" in text and "y" * 5000 in text


def test_page_one_fetches_fifty_one_rows_and_links_to_the_next_page():
    built = with_rows(*[rows.rejected(id=f"v-{i}") for i in range(60)])
    text = get(built).text
    ((_, args),) = [c for c in built.store.calls if c[0] == "orders"]
    assert args == (None, None, None, 51, 0)
    assert text.count("<tr id=") == 50 and 'href="/orders?page=2">Older' in text
    assert "Newer" not in text


def test_page_two_offsets_by_a_page_and_links_back():
    built = with_rows(*[rows.rejected(id=f"v-{i}") for i in range(60)])
    text = get(built, "?page=2").text
    ((_, args),) = [c for c in built.store.calls if c[0] == "orders"]
    assert args[3:] == (51, 50)
    assert text.count("<tr id=") == 10 and 'href="/orders">Newer' in text and "Older" not in text


def test_filters_are_validated_converted_and_echoed():
    built = with_rows(rows.rejected())
    text = get(built, "?symbol=aapl&from=2026-10-19&to=2026-10-20&page=3").text
    ((_, args),) = [c for c in built.store.calls if c[0] == "orders"]
    assert args == (
        "AAPL",
        datetime(2026, 10, 18, 16, 0, tzinfo=UTC),
        datetime(2026, 10, 20, 16, 0, tzinfo=UTC),
        51,
        100,
    )
    assert 'name="symbol" value="AAPL"' in text and 'name="from" value="2026-10-19"' in text


def test_bad_filters_are_ignored_never_errors():
    built = with_rows(rows.rejected())
    response = get(built, "?symbol=%3Cscript%3E&from=nope&to=2026-99-99&page=-1&extra=1")
    assert response.status_code == 200
    ((_, args),) = [c for c in built.store.calls if c[0] == "orders"]
    assert args == (None, None, None, 51, 0)
    assert "<script" not in response.text


def test_an_empty_filter_result_and_a_page_past_the_end_are_lists_not_errors():
    response = get(with_rows(), "?symbol=ZZZ&page=9")
    assert response.status_code == 200 and "No orders match." in response.text


def test_the_shell_is_on_the_page():
    text = get(with_rows(rows.rejected())).text
    assert "Read at " in text and 'aria-current="page">Orders</a>' in text


def test_a_database_failure_is_the_503_page():
    assert get(build(store=failing())).status_code == 503


def test_without_a_session_nothing_is_read():
    built = with_rows(rows.rejected())
    response = built.client(follow_redirects=False).get("/orders")
    assert response.status_code == 303 and built.store.read_calls == 0
    assert "buy AAPL" not in response.text
