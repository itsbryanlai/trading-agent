"""The toggle pages and POST (specs/014 tasks T020; contracts/toggle.md; US2; SC-004, SC-005)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest

from tests.fakes.dashboard_control import FakeControl, failing
from tests.fakes.dashboard_store import FakeStore
from tests.fakes.dashboard_store import failing as failing_store
from tests.unit.dashboard.helpers import Built, build

SINCE = datetime(2026, 10, 5, 21, 12, tzinfo=UTC)
SKIPPED = {"outcome": "skipped", "detail": "trading paused", "slot_key": "morning_session"}
RUNNING_STATE = {
    "trading_paused": False,
    "trading_paused_changed_at": None,
    "daily_loss_halt_active": False,
    "current_trading_date": date(2026, 10, 19),
}
PAUSED_STATE = {**RUNNING_STATE, "trading_paused": True, "trading_paused_changed_at": SINCE}


def schedule(tmp_path, value: str = "false"):
    (tmp_path / "schedule.yaml").write_text(f"portfolio_manager:\n  run_while_paused: {value}\n")
    return tmp_path


def make(tmp_path, *, state=RUNNING_STATE, pm_run=SKIPPED, setting="false", **kwargs):
    store = FakeStore(state=state, pm_run=pm_run)
    control = FakeControl(store=store)
    built = build(store=store, control=control, config_dir=schedule(tmp_path, setting), **kwargs)
    return built, store, control


def get(built: Built, path="/toggle"):
    _, cookie = built.sign_in()
    return built.client().get(path, headers=cookie)


def post(built: Built, form: dict, **headers):
    session, cookie = built.sign_in()
    data = {"csrf": session.csrf, **form}
    return built.client(follow_redirects=False).post(
        "/toggle", data=data, headers={**cookie, **headers}
    )


def toggle_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("dashboard toggle")]


PAUSE = {"expected": "running", "target": "paused"}
RESUME = {"expected": "paused", "target": "running"}


# --- the confirmation page ------------------------------------------------------------------


def test_running_offers_only_the_pause_with_its_text(tmp_path):
    built, _, _ = make(tmp_path)
    text = get(built).text
    assert "<h1>Pause trading?</h1>" in text
    assert "Pausing stops new buys at the gate. Sells and stop-loss exits continue." in text
    assert 'name="expected" value="running"' in text and 'name="target" value="paused"' in text
    assert 'name="csrf"' in text and "Confirm pause" in text and "Cancel" in text
    assert "Confirm resume" not in text


def test_the_pause_page_is_offered_whatever_the_setting_or_evidence(tmp_path):
    built, _, _ = make(tmp_path, setting="true", pm_run=None)
    assert "Confirm pause" in get(built).text


def test_paused_with_everything_clear_offers_resume(tmp_path):
    built, _, _ = make(tmp_path, state=PAUSED_STATE)
    text = get(built).text
    assert "<h1>Resume trading?</h1>" in text
    assert "paused since 6 Oct 05:12 SGT (5 Oct 17:12 ET)" in text
    assert 'name="expected" value="paused"' in text and 'name="target" value="running"' in text
    assert "Confirm resume" in text and "Confirm pause" not in text


def test_a_null_start_time_reads_not_recorded(tmp_path):
    state = {**PAUSED_STATE, "trading_paused_changed_at": None}
    built, _, _ = make(tmp_path, state=state)
    assert "start time not recorded" in get(built).text


@pytest.mark.parametrize(("market", "warned"), [(lambda n: True, True), (lambda n: False, False)])
def test_the_market_warning_follows_the_calendar(tmp_path, market, warned):
    built, _, _ = make(tmp_path, state=PAUSED_STATE, market_open=market)
    assert ("The market is open." in get(built).text) is warned


def test_an_unknown_market_state_still_warns(tmp_path):
    def broken(now):
        raise RuntimeError("calendar down")

    built, _, _ = make(tmp_path, state=PAUSED_STATE, market_open=broken)
    text = get(built).text
    assert "The market is open." in text and "Confirm resume" in text


def test_the_halt_note_appears_only_with_the_halt(tmp_path):
    halted = {**PAUSED_STATE, "daily_loss_halt_active": True}
    built, _, _ = make(tmp_path, state=halted)
    assert "stays active until the next trading day" in get(built).text
    built, _, _ = make(tmp_path, state=PAUSED_STATE)
    assert "stays active until the next trading day" not in get(built).text


@pytest.mark.parametrize(
    ("setting", "pm_run", "phrase"),
    [
        ("true", SKIPPED, "still has run_while_paused: true"),
        ("true", None, "still has run_while_paused: true"),
        ("false", None, "yet skipped a Portfolio Manager run"),
        ("false", {"outcome": "succeeded", "detail": None}, "yet skipped"),
        ("false", {"outcome": "failed", "detail": "trading paused"}, "yet skipped"),
        ("false", {"outcome": "running", "detail": None}, "yet skipped"),
        ("false", {"outcome": "skipped", "detail": "outside session"}, "yet skipped"),
        ("maybe", SKIPPED, "could not be read"),
        ("1", SKIPPED, "could not be read"),
    ],
)
def test_a_refused_resume_shows_the_reason_and_no_form(tmp_path, setting, pm_run, phrase):
    built, _, _ = make(tmp_path, state=PAUSED_STATE, setting=setting, pm_run=pm_run)
    text = get(built).text
    assert "<h1>Resume is not available yet.</h1>" in text and phrase in text
    assert "<form method" not in text.split("</header>")[1]
    assert "Confirm resume" not in text


def test_an_unreadable_schedule_file_refuses_the_resume(tmp_path):
    built, _, _ = make(tmp_path, state=PAUSED_STATE)
    (tmp_path / "schedule.yaml").write_text("portfolio_manager: [broken")
    assert "could not be read" in get(built).text


def test_the_page_needs_a_session(tmp_path):
    built, store, _ = make(tmp_path)
    response = built.client(follow_redirects=False).get("/toggle")
    assert response.status_code == 303 and store.read_calls == 0


def test_a_database_failure_is_the_503_page(tmp_path):
    built = build(store=failing_store(), config_dir=schedule(tmp_path))
    assert get(built).status_code == 503


# --- POST: the allowed paths ----------------------------------------------------------------


def test_a_pause_writes_once_and_redirects(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    built, store, control = make(tmp_path)
    response = post(built, PAUSE)
    assert (
        response.status_code == 303 and response.headers["location"] == "/toggle/done?result=paused"
    )
    assert control.calls == [(False, True)]
    assert toggle_lines(caplog) == ["dashboard toggle paused at=2026-10-19T23:00:00+00:00"]


def test_a_pause_is_never_refused_and_needs_no_read(tmp_path):
    built = build(
        store=failing_store(),
        control=(control := FakeControl()),
        config_dir=tmp_path,  # no schedule file: unreadable
    )
    assert post(built, PAUSE).headers["location"] == "/toggle/done?result=paused"
    assert control.calls == [(False, True)]


def test_a_resume_writes_once_and_redirects(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    built, store, control = make(tmp_path, state=PAUSED_STATE)
    response = post(built, RESUME)
    assert response.headers["location"] == "/toggle/done?result=resumed"
    assert control.calls == [(True, False)]
    assert toggle_lines(caplog) == ["dashboard toggle resumed at=2026-10-19T23:00:00+00:00"]


def test_pause_then_resume_through_the_pages(tmp_path):
    built, store, control = make(tmp_path)
    post(built, PAUSE)
    assert store.state["trading_paused"] is True
    assert "Trading paused." in get(built, "/toggle/done?result=paused").text
    assert "Confirm resume" in get(built).text
    post(built, RESUME)
    assert store.state["trading_paused"] is False
    assert "Trading resumed." in get(built, "/toggle/done?result=resumed").text


# --- POST: refusals write nothing (SC-005) --------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "pm_run", "reason"),
    [
        ("true", SKIPPED, "run_while_paused"),
        ("true", None, "run_while_paused"),
        ("maybe", SKIPPED, "unreadable"),
        ("false", None, "no_skipped_run"),
        ("false", {"outcome": "succeeded", "detail": None}, "no_skipped_run"),
        ("false", {"outcome": "failed", "detail": "trading paused"}, "no_skipped_run"),
        ("false", {"outcome": "running", "detail": None}, "no_skipped_run"),
        ("false", {"outcome": "skipped", "detail": "other"}, "no_skipped_run"),
    ],
)
def test_a_refused_resume_never_calls_the_writer(tmp_path, caplog, setting, pm_run, reason):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    built, _, control = make(tmp_path, state=PAUSED_STATE, setting=setting, pm_run=pm_run)
    response = post(built, RESUME)
    assert response.headers["location"] == "/toggle/done?result=refused"
    assert control.calls == []
    assert toggle_lines(caplog) == [f"dashboard toggle refused reason={reason}"]


def test_the_deployed_setting_is_read_again_at_post_time(tmp_path):
    built, _, control = make(tmp_path, state=PAUSED_STATE)
    assert "Confirm resume" in get(built).text
    schedule(tmp_path, "true")  # the file changes between the page and the POST
    assert post(built, RESUME).headers["location"] == "/toggle/done?result=refused"
    assert control.calls == []


def test_the_pm_run_is_read_again_at_post_time(tmp_path):
    built, store, control = make(tmp_path, state=PAUSED_STATE)
    assert "Confirm resume" in get(built).text
    store.pm_run = {"outcome": "succeeded", "detail": None}  # a run happened after the page
    reads = store.read_calls
    assert post(built, RESUME).headers["location"] == "/toggle/done?result=refused"
    assert store.read_calls == reads + 1  # its own read transaction at POST time
    assert control.calls == []


def test_a_failed_read_at_post_time_refuses_the_resume(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    store = failing_store()
    control = FakeControl()
    built = build(store=store, control=control, config_dir=schedule(tmp_path))
    assert post(built, RESUME).headers["location"] == "/toggle/done?result=refused"
    assert control.calls == []
    assert len(toggle_lines(caplog)) == 1


def test_a_stale_confirmation_calls_the_writer_once_and_changes_nothing(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    store = FakeStore(state=RUNNING_STATE, pm_run=SKIPPED)
    control = FakeControl(result="stale", store=store)
    built = build(store=store, control=control, config_dir=schedule(tmp_path))
    response = post(built, PAUSE)
    assert response.headers["location"] == "/toggle/done?result=stale"
    assert control.calls == [(False, True)]
    assert toggle_lines(caplog) == ["dashboard toggle refused reason=stale"]
    page = get(built, "/toggle/done?result=stale").text
    assert "Nothing was changed." in page and "It is now running." in page


def test_a_control_error_is_failed_and_the_read_pages_still_work(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    store = FakeStore(state=RUNNING_STATE, pm_run=SKIPPED)
    built = build(store=store, control=failing(), config_dir=schedule(tmp_path))
    response = post(built, PAUSE)
    assert response.headers["location"] == "/toggle/done?result=failed"
    assert toggle_lines(caplog) == ["dashboard toggle failed type=ControlError"]
    page = get(built, "/toggle/done?result=failed").text
    assert "The change could not be saved. Trading is still running." in page
    assert get(built, "/").status_code == 200


def test_any_error_from_the_writer_is_failed_with_its_type_only(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    control = FakeControl(error=RuntimeError("MARKER-SECRET detail"))
    built = build(store=FakeStore(), control=control, config_dir=schedule(tmp_path))
    assert post(built, PAUSE).headers["location"] == "/toggle/done?result=failed"
    assert toggle_lines(caplog) == ["dashboard toggle failed type=RuntimeError"]
    assert all("MARKER-SECRET" not in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "form",
    [
        {},
        {"expected": "running"},
        {"target": "paused"},
        {"expected": "paused", "target": "paused"},
        {"expected": "running", "target": "running"},
        {"expected": "running", "target": "resumed"},
        {"expected": "x", "target": "paused"},
        {"expected": "", "target": ""},
        {"expected": "PAUSED", "target": "running"},
    ],
)
def test_a_malformed_form_is_403_with_no_redirect_and_no_write(tmp_path, caplog, form):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    built, _, control = make(tmp_path, state=PAUSED_STATE)
    response = post(built, form)
    assert response.status_code == 403 and "location" not in response.headers
    assert "Request refused. Nothing was changed." in response.text
    assert control.calls == []
    assert toggle_lines(caplog) == ["dashboard toggle refused reason=malformed"]


# --- the guard's own refusals are one line and call nothing ---------------------------------


def test_csrf_origin_and_session_refusals_log_once_and_never_reach_the_writer(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    built, _, control = make(tmp_path)
    session, cookie = built.sign_in()
    client = built.client(follow_redirects=False)
    client.post("/toggle", data={**PAUSE})  # no session
    client.post("/toggle", data={**PAUSE, "csrf": "wrong"}, headers=cookie)
    client.post(
        "/toggle",
        data={**PAUSE, "csrf": session.csrf},
        headers={**cookie, "Origin": "https://evil.example"},
    )
    assert toggle_lines(caplog) == [
        "dashboard toggle refused reason=no_session",
        "dashboard toggle refused reason=csrf",
        "dashboard toggle refused reason=origin",
    ]
    assert control.calls == []


def test_exactly_one_line_per_post_for_every_outcome(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    built, _, _ = make(tmp_path, state=PAUSED_STATE)
    for form in (RESUME, PAUSE, {}):
        caplog.clear()
        post(built, form)
        assert len(toggle_lines(caplog)) == 1


def test_the_log_holds_no_secret_value(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    built, _, _ = make(tmp_path)
    session, _ = built.sign_in()
    post(built, PAUSE)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert session.csrf not in text and session.id not in text


# --- the outcome page -----------------------------------------------------------------------


def test_the_outcome_page_states_the_current_state_read_fresh(tmp_path):
    built, store, _ = make(tmp_path)
    page = get(built, "/toggle/done?result=paused").text
    assert "<h1>Trading paused.</h1>" in page
    assert "Trading is running." in page  # the parameter alone is not trusted


def test_a_refused_outcome_shows_the_reason_from_fresh_reads(tmp_path):
    built, _, _ = make(tmp_path, state=PAUSED_STATE, setting="true")
    page = get(built, "/toggle/done?result=refused").text
    assert "Nothing was changed." in page and "still has run_while_paused: true" in page


def test_an_unknown_result_is_a_404(tmp_path):
    built, _, _ = make(tmp_path)
    for value in ("", "bogus", "<script>"):
        assert get(built, f"/toggle/done?result={value}").status_code == 404
    assert get(built, "/toggle/done").status_code == 404


def test_the_outcome_page_needs_a_session(tmp_path):
    built, _, _ = make(tmp_path)
    response = built.client(follow_redirects=False).get("/toggle/done?result=paused")
    assert response.status_code == 303


def test_no_other_route_calls_the_writer(tmp_path):
    built, _, control = make(tmp_path, state=PAUSED_STATE)
    for path in ("/", "/toggle", "/toggle/done?result=paused", "/sign-in", "/healthz"):
        get(built, path)
    session, cookie = built.sign_in()
    built.client().post("/sign-out", data={"csrf": session.csrf}, headers=cookie)
    assert control.calls == []
