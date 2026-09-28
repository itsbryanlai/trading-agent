"""US4 / FR-013: Execution refuses anything but Alpaca's paper account (research E2).

No network: the SDK clients are either replaced by recorders or constructed
with fake keys and never asked to make a request (tests/conftest.py blocks
the network regardless)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from trading_agent.execution import alpaca as adapter
from trading_agent.execution.broker import BrokerUnavailable, NotPaperTrading, OrderRejected


class _Recorder:
    built: list = []

    def __init__(self, *args, **kwargs):
        type(self).built.append((args, kwargs))
        self._retry, self._retry_codes = 3, [429, 504]
        self._session = SimpleNamespace(request=lambda *a, **k: None)


@pytest.fixture
def recorders(monkeypatch):
    class Trading(_Recorder):
        built = []

    class Data(_Recorder):
        built = []

    monkeypatch.setattr(adapter, "TradingClient", Trading)
    monkeypatch.setattr(adapter, "StockHistoricalDataClient", Data)
    return Trading, Data


def test_the_paper_address_is_fixed_in_code():
    assert adapter.PAPER_TRADING_URL == "https://paper-api.alpaca.markets"


def test_unset_base_url_builds_a_paper_client_at_the_fixed_address(recorders):
    trading, _ = recorders
    adapter.AlpacaBroker("key", "secret", None)
    [(args, kwargs)] = trading.built
    assert kwargs == {"paper": True, "url_override": adapter.PAPER_TRADING_URL}


@pytest.mark.parametrize(
    "configured",
    [
        "https://api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil.com",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets/",
        "https://PAPER-API.alpaca.markets",
    ],
)
def test_any_other_configured_address_refuses_before_building_a_client(recorders, configured):
    trading, data = recorders
    with pytest.raises(NotPaperTrading):
        adapter.AlpacaBroker("key", "secret", configured)
    assert trading.built == [] and data.built == []


def test_the_configured_paper_address_itself_is_accepted(recorders):
    adapter.AlpacaBroker("key", "secret", "https://paper-api.alpaca.markets")


def test_verify_paper_refuses_when_the_account_read_fails(recorders):
    broker = adapter.AlpacaBroker("key", "secret", None)

    def boom():
        raise RuntimeError("401 unauthorized")

    broker._trading.get_account = boom
    with pytest.raises(NotPaperTrading, match="401"):
        broker.verify_paper()


def test_verify_paper_passes_on_a_successful_read_and_logs_the_account(recorders, caplog):
    broker = adapter.AlpacaBroker("key", "secret", None)
    broker._trading.get_account = lambda: SimpleNamespace(account_number="PA123")
    with caplog.at_level(logging.INFO):
        broker.verify_paper()
    assert "PA123" in caplog.text


def test_the_real_sdk_clients_have_no_retries_and_a_timeout():
    # Constructed for real with fake keys; construction makes no request.
    broker = adapter.AlpacaBroker("fake-key", "fake-secret", None)
    for client in (broker._trading, broker._data):
        assert client._retry == 0 and client._retry_codes == []
        assert client._session.request.keywords == {"timeout": adapter.REQUEST_TIMEOUT_SECONDS}
    assert broker._trading._base_url == adapter.PAPER_TRADING_URL


def _api_error(status):
    return adapter.APIError(
        '{"code": 1, "message": "m"}', SimpleNamespace(response=SimpleNamespace(status_code=status))
    )


@pytest.mark.parametrize(("status", "expected"), [(403, OrderRejected), (422, OrderRejected)])
def test_an_outright_refusal_is_order_rejected(recorders, status, expected):
    broker = adapter.AlpacaBroker("key", "secret", None)

    def refuse(_):
        raise _api_error(status)

    broker._trading.submit_order = refuse
    request = adapter.OrderRequest("2026-09-28-AAPL-sell-00000000", "AAPL", "sell", 1, "market")
    with pytest.raises(expected):
        broker.submit_order(request)


@pytest.mark.parametrize("error", [_api_error(504), _api_error(500), TimeoutError("read")])
def test_anything_else_on_submission_means_maybe_placed(recorders, error):
    broker = adapter.AlpacaBroker("key", "secret", None)

    def fail(_):
        raise error

    broker._trading.submit_order = fail
    request = adapter.OrderRequest("2026-09-28-AAPL-sell-00000000", "AAPL", "sell", 1, "market")
    with pytest.raises(BrokerUnavailable, match="may be placed"):
        broker.submit_order(request)


def test_a_missing_order_is_none_and_other_lookup_errors_are_unavailable(recorders):
    broker = adapter.AlpacaBroker("key", "secret", None)

    def missing(_):
        raise _api_error(404)

    broker._trading.get_order_by_client_id = missing
    assert broker.find_order("x") is None

    def down(_):
        raise _api_error(503)

    broker._trading.get_order_by_client_id = down
    with pytest.raises(BrokerUnavailable):
        broker.find_order("x")


@pytest.mark.parametrize("method", ["get_latest_quote", "get_latest_trade"])
def test_a_symbol_missing_from_the_reply_is_unavailable(recorders, method):
    # Second review F6: never a raw KeyError that escapes the monitor's handling.
    broker = adapter.AlpacaBroker("key", "secret", None)
    broker._data.get_stock_latest_quote = lambda request: {}
    broker._data.get_stock_latest_trade = lambda request: {}
    with pytest.raises(BrokerUnavailable):
        getattr(broker, method)("AAPL")
