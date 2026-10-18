"""The app shell (specs/014 tasks T014; contracts/routes.md; SC-003, SC-006).

Probe routes are added to a built app to exercise the guards before any story's routes exist.
The route-access table is copied from contracts/routes.md "Access rule" and grows with each
story: an unlisted route fails `test_every_route_is_in_the_access_table`.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.responses import PlainTextResponse

from tests.unit.dashboard.helpers import SECRET, Built, all_routes, build
from trading_agent.dashboard import app as app_module
from trading_agent.dashboard import auth, deps, render
from trading_agent.dashboard.deps import SessionDep, WriteDep, origin_ok
from trading_agent.dashboard.store import StoreBusy, StoreError

TEMPLATES = Path(app_module.__file__).parent / "templates"
MARKER = "MARKER-STORE-ROW"

# (method, path) -> "open" | "session" (GET needs one) | "write" (POST: session, CSRF, Origin)
ACCESS = {
    ("GET", "/sign-in"): "open",
    ("POST", "/sign-in"): "open",
    ("GET", "/healthz"): "open",
    ("GET", "/static/style.css"): "open",
    ("POST", "/sign-out"): "write",
    ("GET", "/"): "session",
    ("GET", "/orders"): "session",
    ("GET", "/activity"): "session",
    ("GET", "/journal"): "session",
    ("GET", "/toggle"): "session",
    ("POST", "/toggle"): "write",
    ("GET", "/toggle/done"): "session",
}


def _probes(built: Built) -> list[str]:
    """Test-only routes that use the real guards; each write bumps `calls`."""
    app, calls = built.app, []

    @app.get("/probe")
    def read(session: SessionDep):
        return PlainTextResponse(f"{MARKER} csrf={session.csrf}")

    @app.post("/probe")
    def write(session: WriteDep):
        calls.append("probe")
        return PlainTextResponse("written")

    @app.get("/probe/db")
    def database():
        raise StoreError("database: OperationalError")

    @app.get("/probe/busy")
    def busy():
        raise StoreBusy("database: StoreBusy")

    @app.get("/probe/boom")
    def boom():
        raise RuntimeError("MARKER-SECRET postgresql://user:hunter2@host/db")

    @app.get("/probe/escape")
    def escape():
        return deps.render(app.state.deps, "error.html", message='<b onclick="x">&</b>')

    return calls


@pytest.fixture
def built() -> Built:
    return build()


@pytest.fixture
def probed(built):
    built.calls = _probes(built)
    return built


# --- the route-access table (SC-006) --------------------------------------------------------


def test_every_route_is_in_the_access_table(built):
    seen = set()
    for route in all_routes(built.app):
        for method in route.methods - {"HEAD"}:
            seen.add((method, route.path))
    assert {
        ("GET", "/"),
        ("GET", "/healthz"),
        ("GET", "/sign-in"),
        ("POST", "/sign-out"),
        ("GET", "/toggle"),
        ("POST", "/toggle"),
        ("GET", "/toggle/done"),
    } <= seen
    assert seen <= set(ACCESS), (
        f"routes missing from contracts/routes.md: {sorted(seen - set(ACCESS))}"
    )


def test_no_data_without_a_session_on_any_session_route(built):
    built.store.positions_rows = [{"symbol": MARKER}]
    client = built.client(follow_redirects=False)
    registered = {(m, r.path) for r in all_routes(built.app) for m in r.methods}
    checked = []
    for (method, path), rule in ACCESS.items():
        if rule == "open" or (method, path) not in registered:
            continue
        checked.append((method, path))
        response = client.request(method, path)
        assert response.status_code == 303, (method, path)
        assert response.headers["location"] == "/sign-in"
        assert MARKER not in response.text, (method, path)
    assert {
        ("GET", "/"),
        ("GET", "/orders"),
        ("GET", "/activity"),
        ("GET", "/journal"),
        ("POST", "/sign-out"),
    } <= set(checked)
    assert built.store.read_calls == 0


def test_no_documentation_or_schema_routes(built):
    client = built.client()
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404


def test_the_stylesheet_is_open_and_is_css(built):
    response = built.client().get("/static/style.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert (
        "Cache-Control" not in response.headers
        or "no-store" not in response.headers["Cache-Control"]
    )


def test_only_the_stylesheet_is_served_from_static(built):
    client = built.client()
    for path in ("/static/", "/static/base.html", "/static/../app.py", "/static/%2e%2e/app.py"):
        assert client.get(path).status_code == 404


# --- headers on every response (D10) --------------------------------------------------------

CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/static/style.css", 200),
        ("GET", "/nowhere", 404),
        ("POST", "/static/style.css", 404),
        ("GET", "/probe", 303),
        ("POST", "/probe", 303),
        ("GET", "/probe/db", 503),
        ("GET", "/probe/busy", 503),
        ("GET", "/probe/boom", 500),
        ("GET", "/probe/escape", 200),
    ],
)
def test_every_response_carries_the_security_headers(probed, method, path, status):
    response = probed.client(follow_redirects=False).request(method, path)
    assert response.status_code == status
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    if path.startswith("/static/") and status == 200:
        assert response.headers.get("cache-control") != "no-store"
    else:
        assert response.headers["cache-control"] == "no-store"


def test_a_refused_write_carries_the_headers_too(probed):
    _, cookie = probed.sign_in()
    response = probed.client().post("/probe", data={"csrf": "wrong"}, headers=cookie)
    assert response.status_code == 403
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["cache-control"] == "no-store"


# --- error pages show fixed text only -------------------------------------------------------


def test_error_pages_use_fixed_texts_and_log_the_type_and_route_only(probed, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    client = probed.client()
    pages = {
        "/nowhere": (404, "Not found."),
        "/probe/db": (503, "Database unavailable. Nothing is shown until it is back."),
        "/probe/busy": (503, "Database unavailable. Nothing is shown until it is back."),
        "/probe/boom": (500, "Something went wrong."),
    }
    for path, (status, text) in pages.items():
        response = client.get(path)
        assert response.status_code == status
        assert f"<h1>{text}</h1>" in response.text
        for leak in ("MARKER-SECRET", "hunter2", "OperationalError", "RuntimeError", "Traceback"):
            assert leak not in response.text
    lines = [r.getMessage() for r in caplog.records]
    assert "dashboard error type=OperationalError route=/probe/db" in lines
    assert "dashboard error type=StoreBusy route=/probe/busy" in lines
    assert "dashboard error type=RuntimeError route=/probe/boom" in lines
    for line in lines:
        assert "MARKER-SECRET" not in line and "hunter2" not in line


def test_a_500_is_answered_and_never_reaches_the_server_or_the_client(probed, caplog):
    # `raise_server_exceptions=True`: Starlette's ServerErrorMiddleware would re-raise after its
    # handler, and uvicorn would then log the message and traceback (research D18).
    caplog.set_level(logging.DEBUG)
    with caplog.at_level(logging.DEBUG, logger="uvicorn.error"):
        client = probed.client(raise_server_exceptions=True)
        response = client.get("/probe/boom")
    assert response.status_code == 500
    assert "Something went wrong." in response.text
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["cache-control"] == "no-store"
    for record in caplog.records:
        assert record.exc_info is None and record.exc_text is None
        for text in (record.getMessage(), record.name):
            assert "MARKER-SECRET" not in text and "hunter2" not in text
            assert "Traceback" not in text
    errors = [r.getMessage() for r in caplog.records if r.getMessage().startswith("dashboard")]
    assert errors == ["dashboard error type=RuntimeError route=/probe/boom"]


def test_a_500_after_the_response_started_is_logged_not_raised(built, caplog):
    from starlette.responses import StreamingResponse

    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")

    @built.app.get("/probe/partial")
    def partial():
        def chunks():
            yield b"first"
            raise RuntimeError("MARKER-SECRET late")

        return StreamingResponse(chunks())

    built.client(raise_server_exceptions=True).get("/probe/partial")
    lines = [r.getMessage() for r in caplog.records]
    assert "dashboard error type=RuntimeError route=/probe/partial" in lines
    assert all("MARKER-SECRET" not in line for line in lines)


def test_the_log_names_the_route_template_never_the_url(probed, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    probed.client().get("/probe/boom?secret=MARKER-QUERY")
    assert all("MARKER-QUERY" not in r.getMessage() for r in caplog.records)


def test_a_wrong_method_on_a_known_path_is_a_404_not_a_405(probed):
    assert probed.client().put("/probe/db").status_code == 404


# --- templates (SC-003) ---------------------------------------------------------------------


def test_autoescape_is_on(probed):
    response = probed.client().get("/probe/escape")
    assert "&lt;b onclick=&#34;x&#34;&gt;&amp;&lt;/b&gt;" in response.text
    assert "<b onclick" not in response.text


def _template_files() -> list[Path]:
    files = sorted(TEMPLATES.glob("*.html"))
    assert files
    return files


@pytest.mark.parametrize("path", _template_files(), ids=lambda p: p.name)
def test_templates_have_no_script_no_inline_style_and_no_outside_url(path):
    text = path.read_text()
    assert not re.search(r"<script", text, re.IGNORECASE)
    assert not re.search(r"\sstyle\s*=", text, re.IGNORECASE)
    assert not re.search(r"\bon[a-z]+\s*=", text, re.IGNORECASE)
    assert not re.search(r"""(href|src|action)\s*=\s*["']?\s*(https?:)?//""", text, re.IGNORECASE)
    assert "{% autoescape" not in text


@pytest.mark.parametrize("path", _template_files(), ids=lambda p: p.name)
def test_safe_appears_only_where_markdown_output_is_inserted(path):
    for expression in re.findall(r"\{\{.*?\}\}", path.read_text(), re.DOTALL):
        if re.search(r"\|\s*safe\b", expression):
            assert re.search(r"\bmarkdown\(", expression), expression


def test_the_scan_catches_what_it_scans_for():
    bad = '<p style="x"><script>1</script>{{ text|safe }}<a href="https://evil.example">x</a>'
    assert re.search(r"<script", bad) and re.search(r"\sstyle\s*=", bad)
    assert re.search(r"""(href|src|action)\s*=\s*["']?\s*(https?:)?//""", bad)
    assert re.search(r"\|\s*safe\b", bad) and not re.search(r"\bmarkdown\(", "{{ text|safe }}")
    assert render.markdown  # the only source of Markup


def test_the_stylesheet_loads_nothing_from_outside():
    css = (Path(app_module.__file__).parent / "static" / "style.css").read_text()
    assert "url(" not in css and "@import" not in css


# --- sessions and the write guard (D4, D11) -------------------------------------------------


def test_a_valid_session_reads(probed):
    _, cookie = probed.sign_in()
    response = probed.client().get("/probe", headers=cookie)
    assert response.status_code == 200 and MARKER in response.text


def test_no_cookie_a_forged_cookie_and_other_secrets_are_refused(probed):
    session, _ = probed.sign_in()
    client = probed.client(follow_redirects=False)
    name = deps.COOKIE_NAME
    forged = [
        {},
        {"Cookie": f"{name}=garbage"},
        {"Cookie": f"{name}={session.id}"},  # unsigned
        {"Cookie": f"{name}={auth.sign('another-secret-0123456789abcdef012345', session.id)}"},
        {"Cookie": f"{name}={auth.sign(SECRET, 'a-valid-signature-for-no-session')}"},
        {"Cookie": f"{name}={auth.sign(SECRET, session.id)[:-2]}"},
        {"Cookie": f"{name}="},
    ]
    for headers in forged:
        response = client.get("/probe", headers=headers)
        assert response.status_code == 303 and response.headers["location"] == "/sign-in"
        assert MARKER not in response.text


def test_an_expired_session_is_refused_and_one_second_before_it_is_not(probed):
    _, cookie = probed.sign_in()
    client = probed.client(follow_redirects=False)
    probed.clock.advance(hours=12, seconds=-1)
    assert client.get("/probe", headers=cookie).status_code == 200
    probed.clock.advance(seconds=2)
    assert client.get("/probe", headers=cookie).status_code == 303


def test_a_signed_out_session_cannot_be_replayed(probed):
    session, cookie = probed.sign_in()
    probed.sessions.delete(session.id)
    assert probed.client(follow_redirects=False).get("/probe", headers=cookie).status_code == 303


def test_a_post_without_a_session_redirects_and_calls_nothing(probed):
    response = probed.client(follow_redirects=False).post("/probe", data={"csrf": "x"})
    assert response.status_code == 303 and response.headers["location"] == "/sign-in"
    assert probed.calls == []


@pytest.mark.parametrize("form", [{}, {"csrf": ""}, {"csrf": "wrong"}, {"other": "1"}])
def test_a_missing_or_wrong_csrf_is_refused(probed, form):
    _, cookie = probed.sign_in()
    response = probed.client().post("/probe", data=form, headers=cookie)
    assert response.status_code == 403
    assert "Request refused. Nothing was changed." in response.text
    assert probed.calls == []


def test_another_sessions_csrf_value_is_refused(probed):
    _, cookie = probed.sign_in()
    other, _ = probed.sign_in()
    response = probed.client().post("/probe", data={"csrf": other.csrf}, headers=cookie)
    assert response.status_code == 403 and probed.calls == []


def test_a_valid_csrf_with_no_origin_header_passes(probed):
    session, cookie = probed.sign_in()
    response = probed.client().post("/probe", data={"csrf": session.csrf}, headers=cookie)
    assert response.status_code == 200 and probed.calls == ["probe"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "https://testserver.evil.example",
        "https://testserver:8443",
        "http://testserver:80",
        "null",
        "",
        "not a url",
    ],
)
def test_a_cross_origin_post_is_refused_even_with_a_valid_csrf(probed, origin):
    session, cookie = probed.sign_in()
    headers = {**cookie, "Origin": origin}
    response = probed.client().post("/probe", data={"csrf": session.csrf}, headers=headers)
    assert response.status_code == 403 and probed.calls == []


def test_the_production_shape_passes_http_seen_https_sent(probed):
    # Behind Railway's edge the app sees http://host while the browser sends Origin: https://host.
    session, cookie = probed.sign_in()
    client = probed.client(base_url="http://testserver")
    headers = {**cookie, "Origin": "https://testserver"}
    response = client.post("/probe", data={"csrf": session.csrf}, headers=headers)
    assert response.status_code == 200 and probed.calls == ["probe"]


def test_origin_comparison_is_host_and_port_only():
    assert origin_ok(None, "example.test")
    assert origin_ok("https://example.test", "example.test")
    assert origin_ok("http://EXAMPLE.test", "example.test")
    assert origin_ok("https://example.test:8443", "example.test:8443")
    assert not origin_ok("https://example.test:8443", "example.test")
    assert not origin_ok("https://other.test", "example.test")
    assert not origin_ok("https://example.test", None)
    assert not origin_ok("https://example.test", "")
    assert not origin_ok("null", "example.test")
    assert not origin_ok("https://user@example.test", "example.test")
    assert not origin_ok("", "example.test")


def test_a_post_cannot_read_the_session_from_a_query_string_or_header(probed):
    session, cookie = probed.sign_in()
    client = probed.client()
    assert client.post(f"/probe?csrf={session.csrf}", headers=cookie).status_code == 403
    assert client.post("/probe", headers={**cookie, "X-CSRF": session.csrf}).status_code == 403
    assert probed.calls == []


# --- the toggle's refusal log lines (analyze C2) --------------------------------------------


def _toggle_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "toggle" in r.getMessage()]


def test_toggle_refusals_from_the_guard_write_one_line_each(probed, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    client = probed.client(follow_redirects=False)
    session, cookie = probed.sign_in()
    client.post("/toggle", data={"csrf": session.csrf})  # no session
    client.post("/toggle", data={"csrf": "wrong"}, headers=cookie)
    client.post(
        "/toggle", data={"csrf": session.csrf}, headers={**cookie, "Origin": "https://evil.example"}
    )
    assert _toggle_lines(caplog) == [
        "dashboard toggle refused reason=no_session",
        "dashboard toggle refused reason=csrf",
        "dashboard toggle refused reason=origin",
    ]
    assert probed.calls == []


def test_other_routes_write_no_toggle_lines(probed, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    client = probed.client(follow_redirects=False)
    session, cookie = probed.sign_in()
    client.post("/probe", data={"csrf": "wrong"}, headers=cookie)
    client.post("/probe", data={"csrf": session.csrf})
    assert _toggle_lines(caplog) == []
    assert probed.calls == []


def test_the_log_never_holds_a_cookie_csrf_value_or_session_id(probed, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    session, cookie = probed.sign_in()
    client = probed.client(follow_redirects=False)
    client.post("/toggle", data={"csrf": "wrong-MARKER"}, headers=cookie)
    client.get("/probe/boom", headers=cookie)
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (session.id, session.csrf, "wrong-MARKER", SECRET, cookie["Cookie"]):
        assert secret not in text


def test_session_lifetime_matches_the_config(built):
    session = built.sessions.create(built.clock())
    assert session.expires_at - session.created_at == timedelta(hours=12)
