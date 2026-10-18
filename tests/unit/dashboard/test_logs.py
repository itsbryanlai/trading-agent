"""The log guard and the writer guard (specs/014 tasks T030; FR-024, FR-029; research D18).

Markers go into a password, the session secret, a cookie, a CSRF value, a client address, a
query string, stored rationale and broker text, and exception messages; every route is driven at
DEBUG; no marker may reach a log record. Every `POST /toggle` writes exactly one `dashboard
toggle` line, and no route but `POST /toggle` ever reaches the control writer.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.fakes.dashboard_control import FakeControl
from tests.fakes.dashboard_store import FakeStore
from tests.unit.dashboard import activity_rows, order_rows
from tests.unit.dashboard.attribution_fixtures import day as attribution_day
from tests.unit.dashboard.attribution_fixtures import journal_row
from tests.unit.dashboard.helpers import PASSWORD, SECRET, Built, all_routes, build
from trading_agent.dashboard.deps import COOKIE_NAME
from trading_agent.dashboard.store import StoreError

PAUSED = {
    "trading_paused": True,
    "trading_paused_changed_at": datetime(2026, 10, 5, 21, 12, tzinfo=UTC),
    "daily_loss_halt_active": False,
    "current_trading_date": date(2026, 10, 19),
}
SKIPPED = {"outcome": "skipped", "detail": "trading paused", "slot_key": "morning_session"}
MARKERS = {
    "password": "MARKER-PASSWORD-wrong",
    "address": "198.51.100.77-MARKER-ADDR",
    "query": "MARKER-QUERY",
    "rationale": "MARKER-RATIONALE",
    "broker": "MARKER-BROKER-REASON",
    "exception": "MARKER-EXCEPTION-TEXT",
    "database": "MARKER-DB-MESSAGE",
    "control": "MARKER-CONTROL-ERROR",
    "url": "postgresql://user:MARKER-URL-PASSWORD@db.example/prod",
}


def seeded_store(**kwargs) -> FakeStore:
    rationale = f"{MARKERS['rationale']} thesis"
    return FakeStore(
        state=PAUSED,
        pm_run=SKIPPED,
        positions=[
            {"symbol": "AAPL", "qty": Decimal(1), "avg_entry_price": Decimal(2), "updated_at": None}
        ],
        order_rows=[
            order_rows.filled(status="rejected", broker_reason=MARKERS["broker"]),
            order_rows.refused(refusal_details={"note": MARKERS["broker"]}),
        ],
        report_rows=[activity_rows.report(rationale_md=rationale)],
        decision_rows=[activity_rows.decision(reasoning_md=rationale, report_ids=["r-1", "r-9"])],
        run_rows=[activity_rows.timed_out(detail=MARKERS["exception"])],
        report_times_map={"r-9": datetime(2026, 10, 16, 20, 0, tzinfo=UTC)},
        journal_rows=[
            journal_row("2026-10-09", attribution_day("2026-10-09", "0.01"), summary=rationale)
        ],
        journal_range_rows=[
            journal_row("2026-10-09", attribution_day("2026-10-09", "0.01"), summary=rationale)
        ],
        **kwargs,
    )


def make(tmp_path, **kwargs) -> tuple[Built, FakeStore, FakeControl]:
    (tmp_path / "schedule.yaml").write_text("portfolio_manager:\n  run_while_paused: false\n")
    store = seeded_store(**kwargs)
    control = FakeControl(store=store)
    built = build(store=store, control=control, config_dir=tmp_path)

    @built.app.get("/probe/boom")
    def boom():
        raise RuntimeError(f"{MARKERS['exception']} {MARKERS['url']}")

    @built.app.get("/probe/db")
    def database():
        raise StoreError(f"{MARKERS['database']} {MARKERS['url']}")

    return built, store, control


CLIENT_LOGGERS = ("httpx", "httpcore", "asyncio")  # the test client's own, not the service's


def records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if not r.name.startswith(CLIENT_LOGGERS)]


def lines(caplog) -> list[str]:
    return [r.getMessage() for r in records(caplog)]


def everything_logged(caplog) -> str:
    """Every record's message, arguments and exception text, joined."""
    parts = []
    for record in records(caplog):
        parts += [record.getMessage(), str(record.args), record.name, str(record.exc_text or "")]
        parts.append("" if record.exc_info is None else repr(record.exc_info[1]))
    return "\n".join(parts)


def toggle_lines(caplog) -> list[str]:
    return [m for m in lines(caplog) if m.startswith("dashboard toggle")]


def secrets_of(built: Built, session, cookie_value: str) -> list[str]:
    return [
        *MARKERS.values(),
        SECRET,
        built.password_hash,
        session.id,
        session.csrf,
        cookie_value,
        PASSWORD,
    ]


# --- the log guard --------------------------------------------------------------------------


def test_no_marker_reaches_the_log_on_any_route_or_outcome(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    built, store, control = make(tmp_path)
    client = built.client(follow_redirects=False)
    forwarded = {"X-Forwarded-For": f"203.0.113.1, {MARKERS['address']}"}
    q = f"?marker={MARKERS['query']}"

    # Sign-in: failed, too soon, then ok.
    client.post("/sign-in" + q, data={"password": MARKERS["password"]}, headers=forwarded)
    client.post("/sign-in" + q, data={"password": PASSWORD}, headers=forwarded)  # too soon
    built.clock.advance(seconds=10)
    ok = client.post("/sign-in" + q, data={"password": PASSWORD}, headers=forwarded)
    cookie_value = (
        ok.cookies.get(COOKIE_NAME) or ok.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    )
    cookie = {"Cookie": f"{COOKIE_NAME}={cookie_value}"}
    session = next(iter(built.sessions._by_id.values()))
    headers = {**cookie, **forwarded}

    for path in ("/", "/orders", "/activity", "/journal", "/toggle", "/healthz"):
        client.get(path + q, headers=headers)
    for result in ("paused", "resumed", "stale", "refused", "failed"):
        client.get(f"/toggle/done?result={result}&marker={MARKERS['query']}", headers=headers)
    client.get("/activity?agent=runs&problems=1&symbol=AAPL&from=2026-10-01", headers=headers)
    client.get("/nowhere" + q, headers=headers)
    client.get("/probe/boom" + q, headers=headers)
    client.get("/probe/db" + q, headers=headers)

    # A failing database.
    store.error = StoreError(f"{MARKERS['database']} {MARKERS['url']}")
    for path in ("/", "/orders", "/activity", "/journal", "/toggle", "/healthz"):
        client.get(path + q, headers=headers)
    store.error = None

    # Every toggle outcome.
    form = {"csrf": session.csrf, "expected": "paused", "target": "running"}
    client.post("/toggle" + q, data=form, headers=headers)  # resumed
    store.state["trading_paused"] = True
    control.result = "stale"
    client.post("/toggle" + q, data=form, headers=headers)  # stale
    control.result, control.error = "written", RuntimeError(MARKERS["control"] + MARKERS["url"])
    client.post("/toggle" + q, data=form, headers=headers)  # failed
    control.error = None
    client.post("/toggle" + q, data={**form, "target": "paused"}, headers=headers)  # malformed
    client.post("/toggle" + q, data={**form, "csrf": MARKERS["password"]}, headers=headers)
    client.post("/toggle" + q, data=form, headers={**headers, "Origin": "https://evil.example"})
    client.post("/toggle" + q, data=form, headers=forwarded)  # no session
    client.post("/sign-out" + q, data={"csrf": session.csrf}, headers=headers)

    everything = everything_logged(caplog)
    assert lines(caplog), "the drive produced no log lines at all"
    for secret in secrets_of(built, session, cookie_value):
        assert secret not in everything, secret
    for fragment in ("203.0.113.1", "198.51.100.77", "marker=", "postgresql://", "Traceback"):
        assert fragment not in everything, fragment


def test_the_log_has_the_documented_events_and_only_those(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    built, store, control = make(tmp_path)
    client = built.client(follow_redirects=False)
    client.post("/sign-in", data={"password": MARKERS["password"]})
    session, cookie = built.sign_in()
    client.get("/", headers=cookie)
    client.get("/probe/boom", headers=cookie)
    client.get("/probe/db", headers=cookie)
    client.post(
        "/toggle", data={"csrf": session.csrf, "expected": "paused", "target": "running"},
        headers=cookie,
    )  # fmt: skip
    client.post("/sign-out", data={"csrf": session.csrf}, headers=cookie)
    got = lines(caplog)
    assert got == [
        "dashboard sign_in failed site_wait=1 address_wait=1",
        "dashboard error type=RuntimeError route=/probe/boom",
        "dashboard error type=StoreError route=/probe/db",
        "dashboard toggle resumed at=2026-10-19T23:00:00+00:00",
        "dashboard sign_out",
    ]


def test_a_database_message_is_never_the_logged_type(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    built, *_ = make(tmp_path)
    _, cookie = built.sign_in()
    built.client().get("/probe/db", headers=cookie)
    assert "dashboard error type=StoreError route=/probe/db" in lines(caplog)
    assert MARKERS["database"] not in everything_logged(caplog)


# --- exactly one toggle line per POST -------------------------------------------------------

PAUSE = {"expected": "running", "target": "paused"}
RESUME = {"expected": "paused", "target": "running"}


@pytest.mark.parametrize(
    ("name", "state", "form", "expected_line"),
    [
        ("pause", False, PAUSE, "dashboard toggle paused at=2026-10-19T23:00:00+00:00"),
        ("resume", True, RESUME, "dashboard toggle resumed at=2026-10-19T23:00:00+00:00"),
        ("malformed", True, {"expected": "paused", "target": "paused"}, None),
        ("missing", True, {}, None),
    ],
)
def test_each_post_writes_exactly_one_toggle_line(
    tmp_path, caplog, name, state, form, expected_line
):
    caplog.set_level(logging.DEBUG)
    built, store, control = make(tmp_path)
    store.state["trading_paused"] = state
    session, cookie = built.sign_in()
    built.client(follow_redirects=False).post(
        "/toggle", data={"csrf": session.csrf, **form}, headers=cookie
    )
    got = toggle_lines(caplog)
    assert len(got) == 1
    if expected_line:
        assert got == [expected_line]
    else:
        assert got == ["dashboard toggle refused reason=malformed"]


def test_refusals_stale_and_failures_each_write_one_line(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    built, store, control = make(tmp_path)
    session, cookie = built.sign_in()
    client = built.client(follow_redirects=False)

    def post(form=None, headers=None, csrf=None, **kw):
        caplog.clear()
        client.post(
            "/toggle",
            data={"csrf": session.csrf if csrf is None else csrf, **(form or RESUME)},
            headers=headers if headers is not None else cookie,
            **kw,
        )
        return toggle_lines(caplog)

    control.result = "stale"
    assert post() == ["dashboard toggle refused reason=stale"]
    control.result, control.error = "written", RuntimeError(MARKERS["control"])
    assert post() == ["dashboard toggle failed type=RuntimeError"]
    control.error = None
    store.pm_run = {"outcome": "succeeded", "detail": None}
    assert post() == ["dashboard toggle refused reason=no_skipped_run"]
    store.pm_run = SKIPPED
    (tmp_path / "schedule.yaml").write_text("portfolio_manager:\n  run_while_paused: true\n")
    assert post() == ["dashboard toggle refused reason=run_while_paused"]
    (tmp_path / "schedule.yaml").write_text("not yaml: [")
    assert post() == ["dashboard toggle refused reason=unreadable"]
    assert post(csrf="wrong") == ["dashboard toggle refused reason=csrf"]
    assert post(headers={**cookie, "Origin": "https://evil.example"}) == [
        "dashboard toggle refused reason=origin"
    ]
    assert post(headers={}) == ["dashboard toggle refused reason=no_session"]
    assert post(form={"expected": "x", "target": "y"}) == [
        "dashboard toggle refused reason=malformed"
    ]


def test_a_failing_store_on_a_resume_still_writes_one_line(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    built, store, control = make(tmp_path)
    session, cookie = built.sign_in()
    store.error = StoreError("database: OperationalError")
    built.client(follow_redirects=False).post(
        "/toggle", data={"csrf": session.csrf, **RESUME}, headers=cookie
    )
    assert toggle_lines(caplog) == ["dashboard toggle refused reason=no_skipped_run"]
    assert control.calls == []


# --- only POST /toggle reaches the control writer (analyze S7) ------------------------------


def test_no_route_but_post_toggle_ever_calls_the_control_writer(tmp_path):
    built, store, control = make(tmp_path)
    client = built.client(follow_redirects=False)
    driven, answers = [], []
    for route in all_routes(built.app):
        for method in sorted(route.methods - {"HEAD"}):
            if (method, route.path) == ("POST", "/toggle"):
                continue
            driven.append((method, route.path))
            session, cookie = built.sign_in()  # a fresh session each time: sign-out ends one
            kwargs = {"headers": cookie}
            if method == "POST":
                kwargs["data"] = {
                    "csrf": session.csrf,
                    "password": PASSWORD,
                    "expected": "paused",
                    "target": "running",
                }
            answers.append((route.path, client.request(method, route.path, **kwargs).status_code))
            client.request(method, route.path)  # and with no session at all
    assert ("POST", "/sign-in") in driven and ("POST", "/sign-out") in driven
    assert ("GET", "/toggle") in driven and ("GET", "/toggle/done") in driven
    # The drive really reached the pages: a signed-in GET of "/" is a 200, not a redirect.
    assert dict(answers)["/"] == 200 and dict(answers)["/orders"] == 200
    assert control.calls == []
    # With a failing store and a query string, still nothing.
    session, cookie = built.sign_in()
    store.error = StoreError("database: OperationalError")
    for path in ("/", "/orders", "/activity", "/journal", "/toggle", "/toggle/done?result=paused"):
        client.get(path + "?x=1", headers=cookie)
    assert control.calls == []


def test_post_toggle_is_the_only_route_that_does_call_it(tmp_path):
    built, store, control = make(tmp_path)
    session, cookie = built.sign_in()
    built.client(follow_redirects=False).post(
        "/toggle", data={"csrf": session.csrf, **RESUME}, headers=cookie
    )
    assert control.calls == [(True, False)]
