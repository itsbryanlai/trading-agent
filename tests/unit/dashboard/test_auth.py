"""Password hashing, cookie signing, sessions and the CSRF check (specs/014 tasks T006)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.dashboard import auth

SECRET = "fake-secret-not-real-0123456789abcdef0123"
OTHER_SECRET = "another-fake-secret-not-real-abcdef0123456789"
PASSWORD = "correct horse battery staple"
NOW = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)
TWELVE_HOURS = timedelta(hours=12)
FAST = 2**4  # reduced cost for tests; never the production default


def _hash(password: str = PASSWORD, **kwargs) -> str:
    return auth.hash_password(password, n=FAST, **kwargs)


# --- passwords -------------------------------------------------------------------------------


def test_the_default_cost_is_the_documented_one():
    stored = auth.hash_password(PASSWORD)
    assert stored.startswith("scrypt$32768$8$1$")
    assert auth.verify_password(PASSWORD, stored)


def test_a_password_round_trips_and_has_the_documented_shape():
    stored = _hash()
    kind, n, r, p, salt, digest = stored.split("$")
    assert (kind, n, r, p) == ("scrypt", str(FAST), "8", "1")
    assert salt and digest
    assert auth.verify_password(PASSWORD, stored)


def test_a_wrong_password_fails():
    stored = _hash()
    assert not auth.verify_password("correct horse battery stapl", stored)
    assert not auth.verify_password("", stored)
    assert not auth.verify_password(PASSWORD + " ", stored)


def test_two_hashes_of_the_same_password_differ_but_both_verify():
    first, second = _hash(), _hash()
    assert first != second
    assert auth.verify_password(PASSWORD, first) and auth.verify_password(PASSWORD, second)


def test_a_given_salt_makes_the_hash_repeatable():
    assert _hash(salt=b"0123456789abcdef") == _hash(salt=b"0123456789abcdef")


def test_a_non_ascii_password_works():
    stored = _hash("pässwörd-longer-than-16")
    assert auth.verify_password("pässwörd-longer-than-16", stored)
    assert not auth.verify_password("passwoerd-longer-than-16", stored)


def test_a_tampered_hash_fails():
    kind, n, r, p, salt, digest = _hash().split("$")
    flipped = ("A" if digest[0] != "A" else "B") + digest[1:]
    assert not auth.verify_password(PASSWORD, "$".join((kind, n, r, p, salt, flipped)))
    assert not auth.verify_password(PASSWORD, "$".join((kind, n, r, p, "AAAA", digest)))


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not a hash",
        "scrypt$16$8$1$AAAA",  # a field short
        "scrypt$16$8$1$AAAA$AAAA$extra",  # a field over
        "bcrypt$16$8$1$AAAA$AAAA",  # another scheme
        "scrypt$x$8$1$AAAA$AAAA",  # not a number
        "scrypt$16$8$1.5$AAAA$AAAA",
        "scrypt$0$8$1$AAAA$AAAA",  # zero
        "scrypt$-16$8$1$AAAA$AAAA",
        "scrypt$17$8$1$AAAA$AAAA",  # not a power of two
        "scrypt$4294967296$8$1$AAAA$AAAA",  # absurd cost
        "scrypt$16$8$1$!!!!$AAAA",  # not base64
        "scrypt$16$8$1$AAAA$!!!!",
        "scrypt$16$8$1$$AAAA",  # empty salt
        "scrypt$16$8$1$AAAA$",  # empty hash
    ],
)
def test_a_malformed_hash_raises_in_parse_and_verifies_false(stored):
    with pytest.raises(auth.MalformedHash):
        auth.parse_hash(stored)
    assert auth.verify_password(PASSWORD, stored) is False


def test_parse_hash_returns_the_parts():
    parsed = auth.parse_hash(_hash(salt=b"0123456789abcdef"))
    assert (parsed.n, parsed.r, parsed.p) == (FAST, 8, 1)
    assert parsed.salt == b"0123456789abcdef"
    assert len(parsed.digest) == 32


@given(st.text(max_size=60))
def test_arbitrary_text_as_a_stored_hash_never_raises_from_verify(stored):
    assert auth.verify_password(PASSWORD, stored) is False


# --- cookie signing --------------------------------------------------------------------------


def test_sign_and_unsign_round_trip():
    value = auth.sign(SECRET, "abc_DEF-123")
    assert auth.unsign(SECRET, value) == "abc_DEF-123"


def test_the_cookie_value_is_the_id_a_dot_and_a_signature():
    value = auth.sign(SECRET, "abc")
    session_id, _, signature = value.partition(".")
    assert session_id == "abc" and signature and "=" not in signature


def test_another_secret_does_not_unsign():
    assert auth.unsign(OTHER_SECRET, auth.sign(SECRET, "abc")) is None


def test_a_forged_or_truncated_cookie_is_rejected():
    value = auth.sign(SECRET, "abc")
    assert auth.unsign(SECRET, value[:-1]) is None
    assert auth.unsign(SECRET, value + "A") is None
    assert auth.unsign(SECRET, "abd" + value[3:]) is None  # another id, same signature
    assert auth.unsign(SECRET, "abc.") is None
    assert auth.unsign(SECRET, ".sig") is None
    assert auth.unsign(SECRET, "abc") is None
    assert auth.unsign(SECRET, "") is None
    assert auth.unsign(SECRET, auth.sign(SECRET, "")) is None  # an empty id is never valid


@given(st.text(max_size=120))
def test_unsign_only_accepts_what_sign_produced(value):
    session_id = auth.unsign(SECRET, value)
    assert session_id is None or auth.sign(SECRET, session_id) == value


@given(st.text(min_size=1, max_size=40).filter(lambda s: "." not in s))
def test_unsign_inverts_sign_for_any_id_without_a_dot(session_id):
    assert auth.unsign(SECRET, auth.sign(SECRET, session_id)) == session_id


# --- sessions --------------------------------------------------------------------------------


def _sessions(max_sessions: int = 20) -> auth.Sessions:
    return auth.Sessions(max_sessions, TWELVE_HOURS)


def test_a_new_session_has_random_id_and_csrf_and_a_twelve_hour_life():
    sessions = _sessions()
    first, second = sessions.create(NOW), sessions.create(NOW)
    assert first.created_at == NOW and first.expires_at == NOW + TWELVE_HOURS
    assert len({first.id, first.csrf, second.id, second.csrf}) == 4
    assert len(first.id) >= 43 and len(first.csrf) >= 43  # 32 random bytes, base64url


def test_get_returns_the_session_until_it_expires():
    sessions = _sessions()
    session = sessions.create(NOW)
    assert sessions.get(session.id, NOW + TWELVE_HOURS - timedelta(seconds=1)) == session


def test_a_session_one_second_past_twelve_hours_is_gone_and_removed():
    sessions = _sessions()
    session = sessions.create(NOW)
    assert sessions.get(session.id, NOW + TWELVE_HOURS + timedelta(seconds=1)) is None
    assert sessions.get(session.id, NOW) is None  # removed, not just hidden


def test_the_expiry_is_absolute_not_sliding():
    sessions = _sessions()
    session = sessions.create(NOW)
    for minutes in (60, 300, 600, 700):
        sessions.get(session.id, NOW + timedelta(minutes=minutes))
    assert sessions.get(session.id, NOW + TWELVE_HOURS) is None


def test_a_deleted_session_is_gone():
    sessions = _sessions()
    session = sessions.create(NOW)
    sessions.delete(session.id)
    assert sessions.get(session.id, NOW) is None
    sessions.delete(session.id)  # deleting twice is harmless
    sessions.delete("never-existed")


def test_an_unknown_id_is_none():
    assert _sessions().get("nope", NOW) is None


def test_the_twenty_first_session_drops_the_oldest():
    sessions = _sessions(20)
    made = [sessions.create(NOW + timedelta(seconds=i)) for i in range(21)]
    later = NOW + timedelta(minutes=1)
    assert sessions.get(made[0].id, later) is None
    assert all(sessions.get(s.id, later) == s for s in made[1:])


def test_creating_a_session_purges_the_expired_ones_first():
    sessions = _sessions(2)
    old = sessions.create(NOW)
    fresh = sessions.create(NOW + timedelta(hours=11))
    newest = sessions.create(NOW + TWELVE_HOURS + timedelta(minutes=1))
    after = NOW + TWELVE_HOURS + timedelta(minutes=2)
    assert sessions.get(old.id, after) is None
    assert sessions.get(fresh.id, after) == fresh
    assert sessions.get(newest.id, after) == newest


def test_concurrent_creates_never_exceed_the_cap():
    sessions = _sessions(5)
    made = []

    def worker():
        for _ in range(20):
            made.append(sessions.create(NOW))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    live = [s for s in made if sessions.get(s.id, NOW) is not None]
    assert len(live) == 5


# --- CSRF ------------------------------------------------------------------------------------


def test_csrf_accepts_only_the_sessions_own_value():
    session = _sessions().create(NOW)
    assert auth.csrf_ok(session, session.csrf)
    assert not auth.csrf_ok(session, session.csrf[:-1])
    assert not auth.csrf_ok(session, session.csrf + "x")
    assert not auth.csrf_ok(session, _sessions().create(NOW).csrf)


@pytest.mark.parametrize("submitted", [None, "", " "])
def test_an_empty_or_missing_csrf_is_false(submitted):
    session = _sessions().create(NOW)
    assert auth.csrf_ok(session, submitted) is False


def test_no_session_means_no_csrf():
    assert auth.csrf_ok(None, "anything") is False


@given(st.text(max_size=80))
def test_a_non_ascii_csrf_value_is_false_not_an_error(submitted):
    session = _sessions().create(NOW)
    assert auth.csrf_ok(session, submitted) is (submitted == session.csrf)
