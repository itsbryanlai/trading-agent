"""No key header ever follows a redirect (specs/007-research-agent, adversarial review L4).

A real HTTP server on localhost answers every request with a redirect to /landing; the
test fails if /landing is ever reached. Localhost only: the suite's network guard
(tests/conftest.py) allows it.
"""

from __future__ import annotations

import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from trading_agent import no_redirect
from trading_agent.llm.ports import ModelUnavailable
from trading_agent.llm.qwen import QwenClient
from trading_agent.research import finnhub as research_finnhub
from trading_agent.research.ports import ProviderUnavailable

FAKE_KEY = "fake-key-not-real"


@pytest.fixture
def server():
    landed: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            if self.path.startswith("/landing"):
                landed.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"[]")
                return
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/landing")
            self.end_headers()

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", landed
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_opener_refuses_redirects(server):
    base, landed = server
    request = Request(f"{base}/start", headers={"Authorization": f"Bearer {FAKE_KEY}"})
    with pytest.raises(HTTPError) as info:
        no_redirect.open_without_redirects()(request, timeout=5)
    assert info.value.code == 302
    assert landed == []


def test_finnhub_news_doesnt_follow_a_redirect(server, monkeypatch):
    base, landed = server
    monkeypatch.setattr(research_finnhub, "BASE_URL", base)
    news = research_finnhub.FinnhubNews(FAKE_KEY)
    with pytest.raises(ProviderUnavailable):
        news.company_news("AAPL", date(2026, 10, 1), date(2026, 10, 2))
    assert landed == []


def test_qwen_doesnt_follow_a_redirect(server):
    base, landed = server
    client = QwenClient(
        FAKE_KEY,
        model="m",
        max_output_tokens=1000,
        timeout=5,
        base_url=base,
        schema_name="research_answer",
    )
    with pytest.raises(ModelUnavailable) as info:
        client.complete("s", "u", {})
    assert info.value.status == 302
    assert landed == []


def test_the_reference_jobs_finnhub_doesnt_follow_a_redirect(server, monkeypatch):
    from trading_agent.reference import finnhub as reference_finnhub
    from trading_agent.reference.provider import ProviderUnavailable as RefUnavailable

    base, landed = server
    monkeypatch.setattr(reference_finnhub, "BASE_URL", base)
    with pytest.raises(RefUnavailable):
        reference_finnhub.FinnhubProvider(FAKE_KEY).get_quote("AAPL")
    assert landed == []
