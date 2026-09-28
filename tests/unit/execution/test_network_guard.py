"""The suite-wide guard actually blocks the broker (SC-008)."""

import socket

import pytest

from tests.conftest import NetworkBlocked


def test_connecting_to_the_paper_endpoint_fails_the_test():
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("paper-api.alpaca.markets", 443), timeout=1)


def test_a_raw_connect_to_a_public_address_is_blocked():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(NetworkBlocked):
        s.connect(("1.1.1.1", 443))


def test_an_http_client_cannot_reach_the_broker():
    requests = pytest.importorskip("requests")
    with pytest.raises((NetworkBlocked, requests.exceptions.ConnectionError)) as caught:
        requests.get("https://paper-api.alpaca.markets/v2/account", timeout=1)
    assert "paper-api.alpaca.markets" in str(caught.value)
