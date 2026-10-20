"""Sign-in and sign-out (specs/014 tasks T015; US1)."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from http.cookies import SimpleCookie

import pytest

from tests.unit.dashboard.helpers import PASSWORD, SECRET, Built, build
from trading_agent.dashboard import auth
from trading_agent.dashboard.config import load_config
from trading_agent.dashboard.deps import COOKIE_NAME


@pytest.fixture
def built() -> Built:
    return build()


def _sign_in(built: Built, password: str = PASSWORD, **kwargs):
    client = built.client(follow_redirects=False)
    return client, client.post("/sign-in", data={"password": password}, **kwargs)


def _cookie(response) -> SimpleCookie:
    jar = SimpleCookie()
    jar.load(response.headers["set-cookie"])
    return jar


def test_the_form_has_one_password_field(built):
    response = built.client().get("/sign-in")
    assert response.status_code == 200
    assert response.text.count("<input") == 1 and 'name="password"' in response.text
    assert 'action="/sign-in"' in response.text


def test_a_right_password_sets_the_cookie_and_redirects(built):
    _, response = _sign_in(built)
    assert response.status_code == 303 and response.headers["location"] == "/"
    morsel = _cookie(response)[COOKIE_NAME]
    assert morsel["path"] == "/" and morsel["max-age"] == "43200"
    assert morsel["secure"] and morsel["httponly"] and morsel["samesite"].lower() == "strict"
    assert not morsel["domain"]
    raw = response.headers["set-cookie"]
    assert raw.startswith(f"{COOKIE_NAME}=")
    session_id = auth.unsign(SECRET, morsel.value)
    assert session_id and built.sessions.get(session_id, built.clock()) is not None


def test_the_cookie_lifetime_follows_the_config(tmp_path):
    from dataclasses import replace

    config = replace(load_config(), session_hours=3)
    built = build(config=config)
    _, response = _sign_in(built)
    assert _cookie(response)[COOKIE_NAME]["max-age"] == str(3 * 3600)


def test_a_signed_in_visitor_is_sent_home_from_the_form(built):
    _, cookie = built.sign_in()
    response = built.client(follow_redirects=False).get("/sign-in", headers=cookie)
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_a_wrong_password_shows_a_generic_failure_and_no_cookie(built):
    _, response = _sign_in(built, "wrong password")
    assert response.status_code == 200 and "Sign-in failed." in response.text
    assert "set-cookie" not in response.headers
    assert "wrong password" not in response.text


def test_the_failure_never_says_which_part_was_wrong(built):
    _, empty = _sign_in(built, "")
    built.clock.advance(minutes=5)
    _, wrong = _sign_in(built, "x" * 40)
    assert empty.text == wrong.text


def test_a_missing_password_field_fails_like_a_wrong_one(built):
    response = built.client().post("/sign-in", data={})
    assert response.status_code == 200 and "Sign-in failed." in response.text


def test_a_too_early_attempt_is_answered_at_once_and_is_not_counted(built, monkeypatch):
    _sign_in(built, "wrong")
    checked = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, s: checked.append(p) or real(p, s))
    client = built.client(follow_redirects=False)
    for _ in range(5):
        response = client.post("/sign-in", data={"password": PASSWORD})
        assert response.status_code == 429
        assert "Too many attempts. Try again in 2 seconds." in response.text
    assert checked == []  # the password wasn't even checked, so the right one didn't get in
    built.clock.advance(seconds=2)
    response = client.post("/sign-in", data={"password": PASSWORD})
    assert response.status_code == 303  # five early attempts added no delay


def test_failures_double_the_wait(built):
    client = built.client(follow_redirects=False)
    waits = []
    for _ in range(4):
        client.post("/sign-in", data={"password": "wrong"})
        text = client.post("/sign-in", data={"password": "wrong"}).text
        waits.append(int(re.search(r"Try again in (\d+) seconds", text).group(1)))
        built.clock.advance(seconds=waits[-1])
    assert waits == [2, 4, 8, 16]


def test_a_success_clears_the_delay(built):
    _sign_in(built, "wrong")
    built.clock.advance(seconds=2)
    assert _sign_in(built)[1].status_code == 303
    assert _sign_in(built, "wrong")[1].status_code == 200  # checked at once, no wait left


def test_an_error_while_checking_frees_the_slot(built, monkeypatch):
    def boom(password, stored):
        raise RuntimeError("scrypt failed")

    monkeypatch.setattr(auth, "verify_password", boom)
    client = built.client(follow_redirects=False)
    assert client.post("/sign-in", data={"password": "x"}).status_code == 500
    monkeypatch.undo()
    built.clock.advance(seconds=5)
    response = client.post("/sign-in", data={"password": PASSWORD})
    assert response.status_code == 303  # not stuck "in flight"


def test_an_error_creating_the_session_frees_the_slot(built, monkeypatch):
    def boom(now):
        raise RuntimeError("no session")

    monkeypatch.setattr(built.sessions, "create", boom)
    client = built.client(follow_redirects=False)
    assert client.post("/sign-in", data={"password": PASSWORD}).status_code == 500
    monkeypatch.undo()
    built.clock.advance(seconds=5)
    assert client.post("/sign-in", data={"password": PASSWORD}).status_code == 303


def test_the_address_delay_is_per_forwarded_address_using_the_last_entry(built):
    client = built.client(follow_redirects=False)
    client.post(
        "/sign-in", data={"password": "wrong"}, headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9"}
    )
    same = client.post(
        "/sign-in", data={"password": "wrong"}, headers={"X-Forwarded-For": "2.2.2.2, 9.9.9.9"}
    )
    other = client.post(
        "/sign-in", data={"password": "wrong"}, headers={"X-Forwarded-For": "2.2.2.2, 8.8.8.8"}
    )
    assert "in 2 seconds" in same.text  # forging the left entry changes nothing: site + address
    assert "in 1 seconds" in other.text  # another address sees only the site-wide part


def test_a_cross_origin_sign_in_is_refused(built):
    response = built.client().post(
        "/sign-in", data={"password": PASSWORD}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert built.sessions.get("x", built.clock()) is None


def test_a_sign_in_from_the_production_shape_works(built):
    client = built.client(base_url="http://testserver", follow_redirects=False)
    response = client.post(
        "/sign-in", data={"password": PASSWORD}, headers={"Origin": "https://testserver"}
    )
    assert response.status_code == 303


def test_sign_out_removes_the_session_and_a_replayed_cookie_is_no_session(built):
    session, cookie = built.sign_in()
    client = built.client(follow_redirects=False)
    response = client.post("/sign-out", data={"csrf": session.csrf}, headers=cookie)
    assert response.status_code == 303 and response.headers["location"] == "/sign-in"
    morsel = _cookie(response)[COOKIE_NAME]
    assert morsel["max-age"] == "0" and morsel.value == ""
    assert built.sessions.get(session.id, built.clock()) is None
    assert client.get("/sign-in", headers=cookie).status_code == 200  # shown the form again
    replay = client.post("/sign-out", data={"csrf": session.csrf}, headers=cookie)
    assert replay.status_code == 303 and "set-cookie" not in replay.headers


def test_sign_out_needs_the_csrf_value(built):
    session, cookie = built.sign_in()
    response = built.client().post("/sign-out", data={"csrf": "wrong"}, headers=cookie)
    assert response.status_code == 403
    assert built.sessions.get(session.id, built.clock()) is not None


def test_a_session_ends_twelve_hours_after_sign_in(built):
    _, response = _sign_in(built)
    cookie = {"Cookie": f"{COOKIE_NAME}={_cookie(response)[COOKIE_NAME].value}"}
    client = built.client(follow_redirects=False)
    built.clock.advance(hours=12, seconds=-1)
    assert client.get("/sign-in", headers=cookie).status_code == 303  # still signed in
    built.clock.advance(seconds=2)
    assert client.get("/sign-in", headers=cookie).status_code == 200


def test_the_log_has_the_events_and_never_the_password_or_cookie(built, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    client = built.client(follow_redirects=False)
    client.post("/sign-in", data={"password": "MARKER-PASSWORD"})
    client.post("/sign-in", data={"password": PASSWORD})  # too soon
    built.clock.advance(seconds=5)
    ok = client.post("/sign-in", data={"password": PASSWORD})
    session_cookie = ok.headers["set-cookie"]
    built.clock.advance(seconds=1)
    session, headers = built.sign_in()
    client.post("/sign-out", data={"csrf": session.csrf}, headers=headers)
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "dashboard sign_in failed site_wait=1 address_wait=1",
        "dashboard sign_in too_soon wait=2",
        "dashboard sign_in ok",
        "dashboard sign_out",
    ]
    text = "\n".join(lines)
    for secret in ("MARKER-PASSWORD", PASSWORD, built.password_hash, SECRET, session_cookie):
        assert secret not in text


def test_the_pages_carry_the_headers(built):
    response = built.client().get("/sign-in")
    assert response.headers["cache-control"] == "no-store"
    assert "script-src" not in response.headers["content-security-policy"]
    assert timedelta  # keep the import honest
