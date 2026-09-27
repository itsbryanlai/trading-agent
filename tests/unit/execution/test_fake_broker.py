"""The fake broker behaves as later tests assume (contracts/broker-port.md)."""

from decimal import Decimal

import pytest

from tests.fakes.broker import FakeBroker
from trading_agent.execution.broker import BrokerUnavailable, OrderRejected, OrderRequest


def _buy(cid="2026-09-28-AAPL-buy-3f9c2a1b", qty=24, price="201.50"):
    return OrderRequest(cid, "AAPL", "buy", qty, "limit", Decimal(price))


def test_submission_is_recorded_and_findable():
    broker = FakeBroker()
    order = broker.submit_order(_buy())
    assert broker.submissions == [_buy()]
    assert order.status == "new" and order.limit_price == Decimal("201.50")
    assert broker.find_order(_buy().client_order_id) == order
    assert broker.get_order(order.broker_order_id) == order
    assert broker.find_order("nope") is None


def test_fill_partial_then_full_updates_order_position_and_cash():
    broker = FakeBroker(cash="100000")
    broker.submit_order(_buy())
    cid = _buy().client_order_id
    order = broker.fill(cid, 10, "200")
    assert order.status == "partially_filled" and order.filled_qty == 10
    order = broker.fill(cid, 14, "207")
    assert order.status == "filled"
    assert order.filled_avg_price == (10 * Decimal(200) + 14 * Decimal(207)) / 24
    assert broker.positions["AAPL"].qty == 24
    assert broker.account.cash == Decimal(100000) - 2000 - 2898


def test_buy_on_existing_position_uses_a_weighted_average_and_sell_removes_it():
    broker = FakeBroker()
    broker.set_position("AAPL", 24, "200")
    broker.submit_order(_buy(qty=6))
    broker.fill(_buy().client_order_id, 6, "210")
    assert broker.positions["AAPL"].avg_entry_price == Decimal(202)
    sell = OrderRequest("2026-09-28-AAPL-sell-aaaaaaaa", "AAPL", "sell", 30, "market")
    broker.submit_order(sell)
    broker.fill(sell.client_order_id, 30, "190")
    assert "AAPL" not in broker.positions


def test_close_session_cancels_unfilled_and_ends_partial_as_done_for_day():
    broker = FakeBroker()
    broker.submit_order(_buy("a-buy-1"))
    broker.submit_order(_buy("b-buy-2"))
    broker.fill("b-buy-2", 5, "200")
    broker.close_session()
    assert broker.orders_for("a-buy-1")[0].status == "canceled"
    assert broker.orders_for("b-buy-2")[0].status == "done_for_day"


def test_outright_rejection_places_nothing():
    broker = FakeBroker()
    broker.reject_next("insufficient buying power")
    with pytest.raises(OrderRejected, match="insufficient"):
        broker.submit_order(_buy())
    assert broker.submissions == [] and broker.orders == {}


def test_failure_before_and_after_effect():
    broker = FakeBroker()
    broker.fail("get_account")
    with pytest.raises(BrokerUnavailable):
        broker.get_account()
    assert broker.get_account().equity == Decimal(100000)  # only the next call fails

    broker.fail("submit_order")
    with pytest.raises(BrokerUnavailable):
        broker.submit_order(_buy())
    assert broker.submissions == []

    broker.fail("submit_order", after_effect=True)
    with pytest.raises(BrokerUnavailable):
        broker.submit_order(_buy())
    assert len(broker.submissions) == 1  # placed, though the caller saw a timeout


def test_lookup_lag_and_duplicate_client_ids():
    broker = FakeBroker()
    cid = _buy().client_order_id
    broker.hide_from_lookup(cid)
    broker.submit_order(_buy())
    assert broker.find_order(cid) is None
    broker.reveal(cid)
    assert broker.find_order(cid) is not None

    broker.submit_order(_buy())  # duplicates accepted by default
    assert len(broker.orders_for(cid)) == 2
    broker.reject_duplicate_client_ids = True
    with pytest.raises(OrderRejected, match="unique"):
        broker.submit_order(_buy())


def test_missing_quote_or_trade_is_unavailable_and_calls_are_logged():
    broker = FakeBroker()
    with pytest.raises(BrokerUnavailable):
        broker.get_latest_ask("AAPL")
    broker.set_quote("AAPL", "201.50")
    broker.set_trade("AAPL", "201.40")
    assert broker.get_latest_ask("AAPL").ask == Decimal("201.50")
    assert broker.get_latest_trade("AAPL").price == Decimal("201.40")
    assert broker.calls == ["get_latest_ask", "get_latest_ask", "get_latest_trade"]
