"""The Opportunistic Identifier's Finnhub adapter, driven by an injected opener: no socket
is ever opened (specs/011-opportunistic-identifier research O2; contracts/ports.md)."""

from __future__ import annotations

import http.client
import io
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from trading_agent.opportunistic_identifier import finnhub as module
from trading_agent.opportunistic_identifier.finnhub import BASE_URL, OIFinnhub
from trading_agent.opportunistic_identifier.ports import (
    KeyRejected,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)

KEY = "fake-not-real"
T = int(datetime(2026, 10, 8, 14, 55, tzinfo=UTC).timestamp())

# Finnhub metric key -> the Fundamentals field it fills (data-model.md).
METRIC_KEYS = {
    "10DayAverageTradingVolume": "avg_volume_10d_millions",
    "52WeekHigh": "high_52w",
    "52WeekLow": "low_52w",
    "peTTM": "pe_ttm",
    "pbQuarterly": "pb",
    "psTTM": "ps_ttm",
    "grossMarginTTM": "gross_margin_ttm",
    "operatingMarginTTM": "operating_margin_ttm",
    "netProfitMarginTTM": "net_margin_ttm",
    "roeTTM": "roe_ttm",
    "revenueGrowthTTMYoy": "revenue_growth_ttm_yoy",
    "epsGrowthTTMYoy": "eps_growth_ttm_yoy",
    "totalDebt/totalEquityQuarterly": "debt_to_equity",
    "currentDividendYieldTTM": "dividend_yield",
    "beta": "beta",
}


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Opener:
    def __init__(self, body=None, *, error=None, raw=None):
        self.body, self.error, self.raw = body, error, raw
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return Response(self.raw if self.raw is not None else json.dumps(self.body).encode())


class ByMic(Opener):
    """Answers each /stock/symbol request from its `mic`; an exception value is raised."""

    def __init__(self, by_mic):
        super().__init__()
        self.by_mic = by_mic

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        answer = self.by_mic[parse_qs(urlsplit(request.full_url).query)["mic"][0]]
        if isinstance(answer, Exception):
            raise answer
        return Response(json.dumps(answer).encode())


def adapter(opener):
    return OIFinnhub(KEY, opener=opener)


def _url(opener):
    return urlsplit(opener.requests[-1][0].full_url)


def _http_error(code):
    return HTTPError(f"{BASE_URL}/quote?symbol=X", code, "err", {}, io.BytesIO(b"{}"))


# --- the symbol list ------------------------------------------------------------------


def test_the_symbol_list_is_three_requests_merged_with_the_company_name_kept():
    opener = ByMic(
        {
            "XNAS": [
                {
                    "symbol": "AAPL",
                    "type": "Common Stock",
                    "mic": "XNGS",
                    "description": "APPLE INC",
                }
            ],
            "XNYS": [{"symbol": "SPY", "type": "ETP", "mic": "ARCX", "description": "S" * 150}],
            "XASE": [{"type": "Common Stock"}],  # no symbol: skipped
        }
    )
    listings = adapter(opener).us_listings()
    assert set(listings) == {"AAPL", "SPY"}
    assert (listings["AAPL"].type, listings["AAPL"].mic) == ("Common Stock", "XNGS")
    assert listings["AAPL"].description == "APPLE INC"
    assert listings["SPY"].description == "S" * 100
    queries = [parse_qs(urlsplit(r.full_url).query) for r, _ in opener.requests]
    assert sorted(q["mic"][0] for q in queries) == ["XASE", "XNAS", "XNYS"]
    assert all(q["exchange"] == ["US"] for q in queries)
    paths = {urlsplit(r.full_url).path for r, _ in opener.requests}
    assert paths == {"/api/v1/stock/symbol"}


def test_conflicting_duplicate_listings_fail_closed():
    opener = ByMic(
        {
            "XNAS": [{"symbol": "DUAL", "type": "Common Stock", "mic": "XNAS"}],
            "XNYS": [
                {"symbol": "DUAL", "type": "Common Stock", "mic": "XNYS"},
                {"symbol": "SAME", "type": "Common Stock", "mic": "XNYS", "description": "A"},
            ],
            "XASE": [{"symbol": "SAME", "type": "Common Stock", "mic": "XNYS", "description": "A"}],
        }
    )
    listings = adapter(opener).us_listings()
    assert listings["DUAL"].conflicting and listings["DUAL"].type is None
    assert not listings["SAME"].conflicting and listings["SAME"].mic == "XNYS"


@pytest.mark.parametrize("failing", module.SYMBOL_LIST_MICS)
def test_one_exchange_failing_fails_the_whole_list(failing):
    answers = {
        mic: [{"symbol": f"S{mic}", "type": "x", "mic": mic}] for mic in module.SYMBOL_LIST_MICS
    }
    answers[failing] = _http_error(500)
    with pytest.raises(ProviderUnavailable):
        adapter(ByMic(answers)).us_listings()


def test_a_non_list_symbol_list_is_unavailable():
    with pytest.raises(ProviderUnavailable):
        adapter(Opener({"error": "x"})).us_listings()


def test_a_403_on_the_symbol_list_is_the_key_but_on_one_name_is_not_permitted():
    with pytest.raises(KeyRejected):
        adapter(Opener(error=_http_error(403))).us_listings()
    with pytest.raises(NotPermitted):
        adapter(Opener(error=_http_error(403))).quote("X")


# --- quote, profile, fundamentals ------------------------------------------------------------


def test_quote_maps_c_pc_and_t():
    opener = Opener({"c": 151.1, "pc": 150.25, "t": T})
    quote = adapter(opener).quote("AAPL")
    assert (quote.current, quote.previous_close) == (Decimal("151.1"), Decimal("150.25"))
    assert quote.timestamp == datetime(2026, 10, 8, 14, 55, tzinfo=UTC)
    assert _url(opener).path.endswith("/quote")
    assert parse_qs(_url(opener).query) == {"symbol": ["AAPL"]}


def test_profile_maps_cap_currency_and_industry_cut_to_100_characters():
    opener = Opener(
        {"marketCapitalization": 1415993, "currency": "USD", "finnhubIndustry": "I" * 150}
    )
    profile = adapter(opener).profile("AAPL")
    assert profile.market_cap_millions == Decimal("1415993") and profile.currency == "USD"
    assert profile.industry == "I" * 100
    assert _url(opener).path.endswith("/stock/profile2")
    assert parse_qs(_url(opener).query) == {"symbol": ["AAPL"]}


def test_fundamentals_map_every_key_in_the_data_model():
    body = {"metric": {key: index + 1.5 for index, key in enumerate(METRIC_KEYS)}}
    opener = Opener(body)
    got = adapter(opener).fundamentals("AAPL")
    for index, field in enumerate(METRIC_KEYS.values()):
        assert getattr(got, field) == Decimal(str(index + 1.5)), field
    assert _url(opener).path.endswith("/stock/metric")
    assert parse_qs(_url(opener).query) == {"symbol": ["AAPL"], "metric": ["all"]}


def test_empty_bodies_mean_missing_values():
    assert adapter(Opener({})).quote("X").previous_close is None
    assert adapter(Opener({})).profile("X").market_cap_millions is None
    assert adapter(Opener({})).fundamentals("X").high_52w is None
    assert adapter(Opener({"metric": []})).fundamentals("X").high_52w is None


@pytest.mark.parametrize(
    "value", [0, 0.0, "0", "NaN", "inf", "-Infinity", "abc", True, False, None, [1], {"a": 1}]
)
def test_zero_booleans_non_finite_and_wrong_types_become_none(value):
    assert adapter(Opener({"pc": value})).quote("X").previous_close is None
    assert adapter(Opener({"marketCapitalization": value})).profile("X").market_cap_millions is None
    got = adapter(Opener({"metric": {"peTTM": value, "52WeekHigh": value}})).fundamentals("X")
    assert got.pe_ttm is None and got.high_52w is None


@pytest.mark.parametrize("t", [0, -5, "1790593200", True, None, 1e30])
def test_unusable_quote_times_are_none(t):
    assert adapter(Opener({"c": 1, "pc": 1, "t": t})).quote("X").timestamp is None


def test_wrong_typed_text_is_none():
    got = adapter(Opener({"currency": 5, "finnhubIndustry": ["x"]})).profile("X")
    assert got.currency is None and got.industry is None


# --- errors ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(401), KeyRejected),
        (_http_error(403), NotPermitted),
        (_http_error(429), RateLimited),
        (_http_error(404), ProviderUnavailable),
        (_http_error(500), ProviderUnavailable),
        (_http_error(302), ProviderUnavailable),  # a redirect is refused, never followed
        (URLError("no route"), ProviderUnavailable),
        (TimeoutError("slow"), ProviderUnavailable),
        (ConnectionResetError("reset"), ProviderUnavailable),
        (http.client.IncompleteRead(b"par", 10), ProviderUnavailable),
        (http.client.BadStatusLine("junk"), ProviderUnavailable),
        (http.client.LineTooLong("header"), ProviderUnavailable),
    ],
)
def test_errors_map_to_the_port(error, expected):
    with pytest.raises(expected) as caught:
        adapter(Opener(error=error)).quote("X")
    assert KEY not in str(caught.value)


def test_a_401_is_the_key_on_any_call():
    with pytest.raises(KeyRejected):
        adapter(Opener(error=_http_error(401))).profile("X")
    with pytest.raises(KeyRejected):
        adapter(Opener(error=_http_error(401))).us_listings()


def test_a_truncated_body_is_unavailable():
    class Truncated(Response):
        def read(self, *args):
            raise http.client.IncompleteRead(b"par", 10)

    with pytest.raises(ProviderUnavailable):
        adapter(lambda request, timeout: Truncated(b"")).quote("X")


def test_non_json_is_unavailable():
    with pytest.raises(ProviderUnavailable):
        adapter(Opener(raw=b"<html>")).quote("X")


def test_a_non_object_per_name_body_is_unavailable():
    with pytest.raises(ProviderUnavailable):
        adapter(Opener([1, 2])).fundamentals("X")


def test_no_retries_inside_the_adapter():
    opener = Opener(error=_http_error(500))
    with pytest.raises(ProviderUnavailable):
        adapter(opener).quote("X")
    assert len(opener.requests) == 1


def test_the_default_opener_refuses_redirects(monkeypatch):
    made = []

    def fake_factory():
        made.append(True)
        return Opener({})

    monkeypatch.setattr(module, "open_without_redirects", fake_factory)
    OIFinnhub(KEY)
    assert made == [True]


# --- the key ------------------------------------------------------------------------------------


def test_the_key_travels_only_in_the_header_and_the_timeout_is_ten_seconds():
    opener = Opener({"pc": 1})
    adapter(opener).quote("AAPL")
    request, timeout = opener.requests[-1]
    assert request.get_header("X-finnhub-token") == KEY
    assert KEY not in request.full_url
    assert timeout == module.TIMEOUT_SECONDS == 10


def test_repr_str_and_logs_never_show_the_key(caplog):
    a = adapter(Opener(error=_http_error(500)))
    assert KEY not in repr(a) and KEY not in str(a)
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderUnavailable):
        a.quote("X")
    assert all(KEY not in r.getMessage() for r in caplog.records)


def test_fundamentals_carry_the_names_of_every_metric_sent_and_never_a_value():
    body = {"metric": {"peTTM": 20.5, "zzzUnknownMetric": 7, "52WeekHigh": 250, "beta": 1.1}}
    got = adapter(Opener(body)).fundamentals("AAPL")
    assert got.received_keys == ("52WeekHigh", "beta", "peTTM", "zzzUnknownMetric")
    assert "20.5" not in repr(got.received_keys)
    # The names are not part of equality: two fetches with different extras still compare equal.
    other = {"metric": {"peTTM": 20.5, "52WeekHigh": 250, "beta": 1.1}}
    assert got == adapter(Opener(other)).fundamentals("AAPL")


def test_no_metric_object_means_no_received_names():
    assert adapter(Opener({})).fundamentals("X").received_keys == ()
    assert adapter(Opener({"metric": []})).fundamentals("X").received_keys == ()
