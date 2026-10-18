"""The service entry point (specs/014 tasks T018; contracts/dashboard-interface.md)."""

from __future__ import annotations

import logging

import pytest
import uvicorn

from trading_agent.dashboard import __main__ as runner
from trading_agent.dashboard import auth, store

PASSWORD = "correct horse battery staple"
GOOD_HASH = auth.hash_password(PASSWORD, salt=b"0123456789abcdef")  # production cost
SECRETS = {
    "DASHBOARD_DATABASE_URL": "postgresql://reader:MARKER-DB-PASSWORD@db.example/prod",
    "DASHBOARD_CONTROL_DATABASE_URL": "postgresql://writer:MARKER-CONTROL-PASSWORD@db.example/prod",
    "DASHBOARD_PASSWORD_HASH": GOOD_HASH,
    "DASHBOARD_SESSION_SECRET": "MARKER-SESSION-SECRET-0123456789abcdef0123",
    "PORT": "8123",
}


@pytest.fixture
def uvicorn_run(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    return calls


def _answers(monkeypatch, *answers):
    queue = list(answers)
    monkeypatch.setattr(runner.getpass, "getpass", lambda prompt="": queue.pop(0))


# --- flags ----------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["--nope"], ["serve"], ["--hash-password", "--extra"], ["-x"]])
def test_an_unknown_flag_exits_2(argv, uvicorn_run):
    assert runner.main(argv, SECRETS) == 2
    assert uvicorn_run == []


# --- --hash-password ------------------------------------------------------------------------


def test_hash_password_prints_only_the_hash(monkeypatch, capsys, uvicorn_run):
    _answers(monkeypatch, PASSWORD, PASSWORD)
    assert runner.main(["--hash-password"], {}) == 0  # needs no variable, no database
    out = capsys.readouterr()
    assert out.out.startswith("scrypt$32768$8$1$") and out.out.count("\n") == 1
    assert auth.verify_password(PASSWORD, out.out.strip())
    assert PASSWORD not in out.out + out.err
    assert uvicorn_run == []


def test_hash_password_refuses_a_short_password(monkeypatch, capsys):
    _answers(monkeypatch, "fifteen chars!!")
    assert runner.main(["--hash-password"], {}) == 2
    out = capsys.readouterr()
    assert out.out == "" and "fifteen" not in out.err


def test_hash_password_accepts_exactly_sixteen(monkeypatch, capsys):
    _answers(monkeypatch, "x" * 16, "x" * 16)
    assert runner.main(["--hash-password"], {}) == 0


def test_hash_password_refuses_two_entries_that_differ(monkeypatch, capsys):
    _answers(monkeypatch, PASSWORD, PASSWORD + "!")
    assert runner.main(["--hash-password"], {}) == 2
    assert capsys.readouterr().out == ""


# --- serving --------------------------------------------------------------------------------


def test_serving_starts_one_worker_without_access_log_or_proxy_trust(uvicorn_run):
    assert runner.main([], SECRETS) == 0
    ((app, kwargs),) = uvicorn_run
    assert kwargs == {
        "host": "0.0.0.0",
        "port": 8123,
        "workers": 1,
        "access_log": False,
        "proxy_headers": False,
    }
    assert {r.path for r in app.routes if hasattr(r, "path")} >= {"/static/style.css"}


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_a_missing_variable_exits_2_naming_it_and_no_value(name, uvicorn_run, capsys):
    environ = {k: v for k, v in SECRETS.items() if k != name}
    assert runner.main([], environ) == 2
    err = capsys.readouterr().err
    assert name in err
    assert "MARKER" not in err
    assert uvicorn_run == []


def test_an_empty_variable_counts_as_missing(uvicorn_run):
    assert runner.main([], {**SECRETS, "DASHBOARD_SESSION_SECRET": ""}) == 2


def test_a_bad_port_exits_2(uvicorn_run, capsys):
    assert runner.main([], {**SECRETS, "PORT": "eighty"}) == 2
    assert "PORT" in capsys.readouterr().err and uvicorn_run == []


def test_a_bad_config_exits_2(monkeypatch, uvicorn_run, capsys):
    def broken():
        raise runner.DashboardConfigError("page_size: 5 is outside 10-200")

    monkeypatch.setattr(runner, "load_config", broken)
    assert runner.main([], SECRETS) == 2
    assert "page_size" in capsys.readouterr().err and uvicorn_run == []


def test_the_store_gets_the_read_url_and_the_configured_timeout(monkeypatch, uvicorn_run):
    seen = {}

    class Spy(store.PostgresStore):
        def __init__(self, url, timeout):
            seen["args"] = (url, timeout)
            super().__init__(url, timeout)

    monkeypatch.setattr(runner, "PostgresStore", Spy)
    runner.main([], SECRETS)
    assert seen["args"] == (SECRETS["DASHBOARD_DATABASE_URL"], 10)


def test_the_app_gets_the_control_writer_from_the_environment(uvicorn_run):
    runner.main([], SECRETS)
    ((app, _),) = uvicorn_run
    control = app.state.deps.control
    assert isinstance(control, runner.ControlWriter)
    assert "MARKER" not in repr(control)


def test_the_started_and_stopped_lines_hold_no_variable_value(uvicorn_run, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")
    runner.main([], SECRETS)
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0].startswith("dashboard started port=8123 config_version=")
    assert len(lines[0].rsplit("=", 1)[1]) == 8
    assert lines[-1] == "dashboard stopped"
    for value in SECRETS.values():
        if value != SECRETS["PORT"]:
            assert all(value not in line for line in lines)


def test_stopped_is_logged_even_if_the_server_raises(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="trading_agent.dashboard")

    def explode(app, **kwargs):
        raise OSError("address in use")

    monkeypatch.setattr(uvicorn, "run", explode)
    with pytest.raises(OSError):
        runner.main([], SECRETS)
    assert caplog.records[-1].getMessage() == "dashboard stopped"


# --- start-up refuses weak or mistyped values (review of T018) -------------------------------


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("DASHBOARD_SESSION_SECRET", "x" * 31, "too_short"),
        ("DASHBOARD_SESSION_SECRET", "a" * 32, "too_short"),
        ("DASHBOARD_SESSION_SECRET", "ab" * 40, "too_short"),
        ("DASHBOARD_PASSWORD_HASH", "scrypt$16$8$1$AAAA$AAAA", "malformed"),
        ("DASHBOARD_PASSWORD_HASH", "not a hash MARKER-HASH", "malformed"),
        (
            "DASHBOARD_PASSWORD_HASH",
            "scrypt$1048576$8$1$MDEyMzQ1Njc4OWFiY2RlZg==$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ=",
            "malformed",
        ),
        (
            "DASHBOARD_PASSWORD_HASH",
            "scrypt$32768$32$1$MDEyMzQ1Njc4OWFiY2RlZg==$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ=",
            "malformed",
        ),
    ],
)
def test_serving_refuses_a_weak_secret_or_a_malformed_hash(name, value, code, uvicorn_run, capsys):
    assert runner.main([], {**SECRETS, name: value}) == 2
    err = capsys.readouterr().err
    assert name in err and code in err and "MARKER" not in err and value not in err
    assert uvicorn_run == []


def test_a_thirty_two_byte_secret_with_variety_is_accepted(uvicorn_run):
    value = "0123456789abcdefghijklmnopqrstuv"
    assert runner.main([], {**SECRETS, "DASHBOARD_SESSION_SECRET": value}) == 0
