"""Research's Finnhub news adapter, driven by an injected opener: no socket is ever
opened (specs/007-research-agent research R3)."""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from trading_agent.reference.finnhub import FinnhubProvider
from trading_agent.reference.provider import KeyRejected as RefKeyRejected
from trading_agent.reference.provider import NotPermitted as RefNotPermitted
from trading_agent.reference.provider import ProviderUnavailable as RefUnavailable
from trading_agent.reference.provider import RateLimited as RefRateLimited
from trading_agent.research.finnhub import BASE_URL, FinnhubNews
from trading_agent.research.ports import (
    KeyRejected,
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)

KEY = "test-key-not-real"
T = int(datetime(2026, 10, 1, 11, 0, tzinfo=UTC).timestamp())


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


def item(**changes):
    row = {
        "id": 1,
        "category": "company",
        "datetime": T,
        "headline": "Apple beats",
        "related": "AAPL",
        "source": "Example Wire",
        "summary": "Apple reported.",
        "url": "https://news.example.com/apple",
    }
    row.update(changes)
    return row


def http_error(code):
    return HTTPError(f"{BASE_URL}/x", code, "err", {}, None)


def test_general_news_request_and_mapping():
    opener = Opener([item(related="AAPL, msft,")])
    (article,) = FinnhubNews(KEY, opener=opener).general_news()
    request, timeout = opener.requests[0]
    parts = urlsplit(request.full_url)
    assert parts.path == "/api/v1/news" and parse_qs(parts.query) == {"category": ["general"]}
    assert request.get_header("X-finnhub-token") == KEY and KEY not in request.full_url
    assert timeout == 10
    assert article.url == "https://news.example.com/apple"
    assert article.headline == "Apple beats" and article.summary == "Apple reported."
    assert article.source == "Example Wire"
    assert article.published_at == datetime(2026, 10, 1, 11, 0, tzinfo=UTC)
    assert article.related == ("AAPL", "MSFT")


def test_company_news_request_uses_the_dates():
    opener = Opener([item()])
    FinnhubNews(KEY, opener=opener).company_news("MSFT", date(2026, 9, 30), date(2026, 10, 1))
    parts = urlsplit(opener.requests[0][0].full_url)
    assert parts.path == "/api/v1/company-news"
    assert parse_qs(parts.query) == {
        "symbol": ["MSFT"],
        "from": ["2026-09-30"],
        "to": ["2026-10-01"],
    }


def test_symbol_list():
    opener = Opener([{"symbol": "AAPL"}, {"symbol": "BRK.B"}, {"nope": 1}, "x"])
    assert FinnhubNews(KEY, opener=opener).us_symbols() == frozenset({"AAPL", "BRK.B"})
    parts = urlsplit(opener.requests[0][0].full_url)
    assert parts.path == "/api/v1/stock/symbol" and parse_qs(parts.query) == {"exchange": ["US"]}


@pytest.mark.parametrize(
    "bad",
    [
        {"url": None},
        {"url": "javascript:alert(1)"},
        {"url": "data:text/html,hi"},
        {"url": "ftp://news.example.com/x"},
        {"url": "https://"},
        {"url": " https://news.example.com/space"},
        {"headline": ""},
        {"headline": None},
        {"datetime": 0},
        {"datetime": "1759316400"},
        {"datetime": True},
    ],
)
def test_items_that_cant_be_cited_safely_are_skipped(bad):
    opener = Opener([item(**bad), item(url="https://news.example.com/ok")])
    articles = FinnhubNews(KEY, opener=opener).general_news()
    assert [a.url for a in articles] == ["https://news.example.com/ok"]


def test_missing_summary_and_source_become_empty_text():
    (article,) = FinnhubNews(KEY, opener=Opener([item(summary=None, source=5)])).general_news()
    assert (article.summary, article.source) == ("", "")


@pytest.mark.parametrize(
    ("call", "code", "expected"),
    [
        ("general", 401, KeyRejected),
        ("general", 403, KeyRejected),
        ("symbols", 403, KeyRejected),
        ("company", 401, KeyRejected),
        ("company", 403, NotPermitted),
        ("company", 429, RateLimited),
        ("general", 429, RateLimited),
        ("general", 500, ProviderUnavailable),
    ],
)
def test_http_errors(call, code, expected):
    news = FinnhubNews(KEY, opener=Opener(error=http_error(code)))
    with pytest.raises(expected) as info:
        _call(news, call)
    assert KEY not in str(info.value)


def _call(news, call):
    if call == "general":
        return news.general_news()
    if call == "symbols":
        return news.us_symbols()
    return news.company_news("AAPL", date(2026, 9, 30), date(2026, 10, 1))


@pytest.mark.parametrize("opener", [Opener(error=URLError("down")), Opener(error=TimeoutError())])
def test_network_errors_are_unavailable(opener):
    with pytest.raises(ProviderUnavailable):
        FinnhubNews(KEY, opener=opener).general_news()


@pytest.mark.parametrize("raw", [b"not json", b'{"error": "x"}'])
def test_unreadable_bodies_are_unavailable(raw):
    with pytest.raises(ProviderUnavailable):
        FinnhubNews(KEY, opener=Opener(raw=raw)).general_news()


def test_the_key_is_hidden():
    news = FinnhubNews(KEY)
    assert KEY not in repr(news) and KEY not in str(news)


_SAME = {
    RefKeyRejected: KeyRejected,
    RefNotPermitted: NotPermitted,
    RefRateLimited: RateLimited,
    RefUnavailable: ProviderUnavailable,
}


@pytest.mark.parametrize("code", [401, 403, 429, 500])
@pytest.mark.parametrize("per_symbol", [False, True])
def test_status_mapping_matches_the_reference_adapter(code, per_symbol):
    ref = FinnhubProvider(KEY, opener=Opener(error=http_error(code)))
    ours = FinnhubNews(KEY, opener=Opener(error=http_error(code)))
    with pytest.raises(Exception) as ref_error:
        ref.get_quote("AAPL") if per_symbol else ref.list_us_symbols()
    with pytest.raises(Exception) as our_error:
        _call(ours, "company" if per_symbol else "symbols")
    assert _SAME[type(ref_error.value)] is type(our_error.value)
