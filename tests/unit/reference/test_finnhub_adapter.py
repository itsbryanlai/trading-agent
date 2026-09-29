"""The Finnhub adapter, driven by an injected opener: no socket is ever opened
(research D6, D14). Canned bodies copy the field names in Finnhub's published
API description."""

from __future__ import annotations

import io
import json
import logging
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from trading_agent.reference.finnhub import BASE_URL, FinnhubProvider
from trading_agent.reference.provider import KeyRejected, ProviderUnavailable, RateLimited

KEY = "test-key-not-real"


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Opener:
    def __init__(self, body=None, *, error=None, raw=None):
        self.body = body
        self.error = error
        self.raw = raw
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        if self.raw is not None:
            return Response(self.raw)
        return Response(json.dumps(self.body).encode())


def provider(opener):
    return FinnhubProvider(KEY, opener=opener)


def _url(opener):
    request, _ = opener.requests[-1]
    return urlsplit(request.full_url)


def test_symbol_list():
    opener = Opener(
        [
            {"symbol": "AAPL", "type": "Common Stock", "mic": "XNGS", "currency": "USD"},
            {"symbol": "SPY", "type": "ETP", "mic": "ARCX"},
            {"type": "Common Stock"},  # no symbol: skipped
        ]
    )
    listings = provider(opener).list_us_symbols()
    assert set(listings) == {"AAPL", "SPY"}
    assert listings["AAPL"].type == "Common Stock" and listings["AAPL"].mic == "XNGS"
    url = _url(opener)
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{BASE_URL}/stock/symbol"
    assert parse_qs(url.query) == {"exchange": ["US"]}


def test_profile_quote_metrics_paths_and_values():
    opener = Opener({"marketCapitalization": 1415993, "name": "Apple Inc"})
    assert provider(opener).get_profile("AAPL").market_cap_millions == Decimal("1415993")
    assert _url(opener).path.endswith("/stock/profile2")
    assert parse_qs(_url(opener).query) == {"symbol": ["AAPL"]}

    opener = Opener({"c": 151.1, "pc": 150.25, "t": 1})
    assert provider(opener).get_quote("AAPL").previous_close == Decimal("150.25")
    assert _url(opener).path.endswith("/quote")
    assert parse_qs(_url(opener).query) == {"symbol": ["AAPL"]}

    opener = Opener({"metric": {"10DayAverageTradingVolume": 32.50147}, "symbol": "AAPL"})
    assert provider(opener).get_metrics("AAPL").avg_volume_10d_millions == Decimal("32.50147")
    assert _url(opener).path.endswith("/stock/metric")
    assert parse_qs(_url(opener).query) == {"symbol": ["AAPL"], "metric": ["all"]}


def test_empty_objects_mean_missing_values():
    assert provider(Opener({})).get_profile("X").market_cap_millions is None
    assert provider(Opener({})).get_quote("X").previous_close is None
    assert provider(Opener({})).get_metrics("X").avg_volume_10d_millions is None
    assert provider(Opener({"metric": []})).get_metrics("X").avg_volume_10d_millions is None


@pytest.mark.parametrize("value", ["NaN", "inf", "-Infinity", "abc", True, None, [1], {"a": 1}])
def test_unusable_numbers_become_none(value):
    assert provider(Opener({"pc": value})).get_quote("X").previous_close is None


def test_the_key_travels_only_in_the_header():
    opener = Opener({"pc": 1})
    provider(opener).get_quote("AAPL")
    request, timeout = opener.requests[-1]
    assert request.get_header("X-finnhub-token") == KEY
    assert KEY not in request.full_url
    assert timeout == 10


def _http_error(code):
    return HTTPError(f"{BASE_URL}/quote?symbol=X", code, "err", {}, io.BytesIO(b"{}"))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(401), KeyRejected),
        (_http_error(403), KeyRejected),
        (_http_error(429), RateLimited),
        (_http_error(404), ProviderUnavailable),
        (_http_error(500), ProviderUnavailable),
        (_http_error(503), ProviderUnavailable),
        (URLError("no route"), ProviderUnavailable),
        (TimeoutError("slow"), ProviderUnavailable),
        (ConnectionResetError("reset"), ProviderUnavailable),
    ],
)
def test_errors_map_to_the_port(error, expected):
    with pytest.raises(expected) as caught:
        provider(Opener(error=error)).get_quote("X")
    assert KEY not in str(caught.value)


def test_bad_json_is_unavailable():
    with pytest.raises(ProviderUnavailable):
        provider(Opener(raw=b"<html>")).get_quote("X")


def test_a_non_list_symbol_list_is_unavailable():
    with pytest.raises(ProviderUnavailable):
        provider(Opener({"error": "x"})).list_us_symbols()


def test_repr_and_logs_never_show_the_key(caplog):
    p = provider(Opener(error=_http_error(500)))
    assert KEY not in repr(p) and KEY not in str(p)
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderUnavailable):
        p.get_quote("X")
    assert all(KEY not in r.getMessage() for r in caplog.records)


def test_no_retries_inside_the_adapter():
    opener = Opener(error=_http_error(500))
    with pytest.raises(ProviderUnavailable):
        provider(opener).get_quote("X")
    assert len(opener.requests) == 1
