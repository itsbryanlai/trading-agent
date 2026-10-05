"""The login command, offline: arguments, strings, exit codes, no admin secret in output."""

from __future__ import annotations

from contextlib import contextmanager

import psycopg
import pytest

from trading_agent.storage import logins
from trading_agent.storage.logins import LOGINS, main, parse_args, split_host

ADMIN_PASSWORD = "S3cret-admin-pw"
ADMIN_URL = f"postgresql://admin:{ADMIN_PASSWORD}@public.example:5432/railway"
GROUPS = {login.group for login in LOGINS}


class FakeConnection:
    """Answers the two role lookups and records the other statements."""

    def __init__(self, groups, existing=()):
        self.groups = set(groups)
        self.existing = set(existing)
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        if params is None:
            self.statements.append(statement.as_string(None))
            return None
        wanted = set(params[0])
        known = self.groups if wanted & GROUPS else self.existing
        rows = [{"rolname": n} for n in sorted(wanted & known)]
        return type("Result", (), {"fetchall": lambda self: rows})()


def _connector(conn=None, error=None):
    @contextmanager
    def connect(url):
        assert url == ADMIN_URL
        if error:
            raise error
        yield conn

    return connect


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_DATABASE_URL", ADMIN_URL)


def test_the_login_table_is_the_contracts_eight_rows():
    assert [tuple(login) for login in LOGINS] == [
        ("ta_orchestrator_login", "ta_orchestrator"),
        ("ta_research_login", "ta_research"),
        ("ta_portfolio_manager_login", "ta_portfolio_manager"),
        ("ta_risk_gate_login", "ta_risk_gate"),
        ("ta_reference_data_login", "ta_reference_data"),
        ("ta_execution_login", "ta_execution"),
        ("ta_owner_read_login", "ta_dashboard"),
        ("ta_owner_control_login", "ta_dashboard_control"),
    ]


def test_the_port_defaults_to_5432():
    assert parse_args(["--service-host", "db.internal"]) == ("db.internal", 5432, [])
    assert parse_args(["--service-host", "db.internal:6543"]) == ("db.internal", 6543, [])


@pytest.mark.parametrize("value", ["", "a:b", "a:0", "a:70000", "a:1:2", "a/b", "u@a", "[::1"])
def test_a_bad_host_is_refused(value):
    with pytest.raises(logins.LoginsError):
        split_host(value)


def test_an_ipv6_host_keeps_its_brackets():
    assert split_host("[::1]:5433") == ("[::1]", 5433)


def test_reset_names_are_checked_and_deduplicated():
    assert parse_args(["--service-host", "h", "--reset", "ta_execution_login"] * 2)[2] == [
        "ta_execution_login"
    ]
    with pytest.raises(logins.LoginsError):
        parse_args(["--service-host", "h", "--reset", "ta_nobody_login"])


def test_a_missing_service_host_exits_2(capsys):
    assert main([]) == 2
    assert "service-host" in capsys.readouterr().err


def test_an_unknown_reset_name_exits_2_before_any_connection(capsys):
    assert (
        main(["--service-host", "h", "--reset", "nope"], connect_fn=_connector(error=OSError)) == 2
    )


def test_a_missing_admin_variable_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_DATABASE_URL")
    assert main(["--service-host", "h"]) == 2
    assert "ADMIN_DATABASE_URL" in capsys.readouterr().err


def test_the_connection_string_quotes_the_password():
    url = logins.connection_string("ta_x_login", "p@ss/word:1 %", "h", 5432, "my db")
    assert url == "postgresql://ta_x_login:p%40ss%2Fword%3A1%20%25@h:5432/my%20db"


def test_passwords_are_32_random_bytes_urlsafe():
    first, second = logins.new_password(), logins.new_password()
    assert first != second and len(first) == 43


def test_a_fresh_run_creates_all_eight_and_prints_each_string_once(capsys):
    conn = FakeConnection(GROUPS)
    assert main(["--service-host", "svc.internal:5433"], connect_fn=_connector(conn)) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 8
    for line, login in zip(out, LOGINS, strict=True):
        name, url = line.split("  ")
        assert name == login.name
        assert url.startswith(f"postgresql://{login.name}:") and url.endswith(
            "@svc.internal:5433/railway"
        )
    creates = [s for s in conn.statements if s.startswith("CREATE ROLE")]
    assert len(creates) == 8
    assert all("LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '" in s for s in creates)
    assert 'IN ROLE "ta_dashboard_control"' in creates[-1]


def test_existing_logins_are_left_alone(capsys):
    conn = FakeConnection(GROUPS, existing={"ta_research_login", "ta_execution_login"})
    assert main(["--service-host", "h"], connect_fn=_connector(conn)) == 0
    out = capsys.readouterr().out
    assert "ta_research_login  exists, unchanged" in out
    assert "ta_execution_login  exists, unchanged" in out
    assert len([s for s in conn.statements if s.startswith("CREATE ROLE")]) == 6
    assert not any(s.startswith("ALTER ROLE") for s in conn.statements)


def test_reset_changes_only_the_named_login(capsys):
    names = {login.name for login in LOGINS}
    conn = FakeConnection(GROUPS, existing=names)
    assert (
        main(["--service-host", "h", "--reset", "ta_risk_gate_login"], connect_fn=_connector(conn))
        == 0
    )
    assert [s.split()[2] for s in conn.statements] == ['"ta_risk_gate_login"']
    out = capsys.readouterr().out.splitlines()
    assert sum("exists, unchanged" in line for line in out) == 7


def test_reset_of_a_login_that_does_not_exist_exits_2():
    conn = FakeConnection(GROUPS)
    assert (
        main(["--service-host", "h", "--reset", "ta_risk_gate_login"], connect_fn=_connector(conn))
        == 2
    )
    assert conn.statements == []


def test_a_missing_group_role_exits_2_and_creates_nothing(capsys):
    conn = FakeConnection(GROUPS - {"ta_execution"})
    assert main(["--service-host", "h"], connect_fn=_connector(conn)) == 2
    assert conn.statements == []
    captured = capsys.readouterr()
    assert "ta_execution" in captured.err and "run migrate first" in captured.err
    assert captured.out == ""


def test_an_unreachable_database_exits_3_without_the_driver_message(capsys):
    error = psycopg.OperationalError(f"could not connect admin:{ADMIN_PASSWORD}@public.example")
    assert main(["--service-host", "h"], connect_fn=_connector(error=error)) == 3
    captured = capsys.readouterr()
    assert ADMIN_PASSWORD not in captured.err + captured.out and "OperationalError" in captured.err


def test_another_database_error_exits_1_and_prints_no_text_from_it(capsys):
    error = psycopg.errors.InsufficientPrivilege(f"permission denied {ADMIN_PASSWORD}")
    assert main(["--service-host", "h"], connect_fn=_connector(error=error)) == 1
    captured = capsys.readouterr()
    assert ADMIN_PASSWORD not in captured.err + captured.out


def test_output_never_contains_the_admin_password_or_url(capsys):
    conn = FakeConnection(GROUPS, existing={"ta_research_login"})
    main(["--service-host", "h", "--reset", "ta_research_login"], connect_fn=_connector(conn))
    captured = capsys.readouterr()
    assert ADMIN_PASSWORD not in captured.out + captured.err
    assert "public.example" not in captured.out + captured.err
    assert "admin" not in captured.out.replace("ta_dashboard", "")


def test_a_bad_admin_url_is_refused_without_quoting_it(monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_DATABASE_URL", f"admin {ADMIN_PASSWORD} nonsense")
    assert main(["--service-host", "h"]) == 2
    assert ADMIN_PASSWORD not in capsys.readouterr().err
