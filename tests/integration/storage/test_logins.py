"""The login command against a migrated database (contracts/logins-command.md).

Logins are cluster-wide, not per database, so the fixture drops every login it could
have created, before and after each test, even a failed one.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from tests.integration.storage.grants_matrix import GRANTS
from trading_agent.storage import logins
from trading_agent.storage.logins import LOGINS

NAMES = [login.name for login in LOGINS]
# A literal copy of the contract's rows (contracts/logins-command.md), not derived from
# LOGINS, so a swapped group in the command shows up here.
GROUP_OF = {
    "ta_orchestrator_login": "ta_orchestrator",
    "ta_research_login": "ta_research",
    "ta_opportunistic_identifier_login": "ta_opportunistic_identifier",
    "ta_portfolio_manager_login": "ta_portfolio_manager",
    "ta_risk_gate_login": "ta_risk_gate",
    "ta_reference_data_login": "ta_reference_data",
    "ta_execution_login": "ta_execution",
    "ta_owner_read_login": "ta_dashboard",
    "ta_owner_control_login": "ta_dashboard_control",
    "ta_journal_login": "ta_journal",
}


def _drop_all(server_url: str) -> None:
    with psycopg.connect(server_url, autocommit=True) as admin:
        for name in NAMES:
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.fixture
def run(database_url, server_url, monkeypatch, capsys):
    _drop_all(server_url)
    monkeypatch.setenv("ADMIN_DATABASE_URL", database_url)
    parts = conninfo_to_dict(database_url)
    service_host = f"{parts['host']}:{parts['port']}"

    def _run(*extra: str) -> tuple[int, dict[str, str]]:
        code = logins.main(["--service-host", service_host, *extra])
        out = capsys.readouterr().out
        lines = dict(line.split("  ", 1) for line in out.splitlines())
        return code, lines

    try:
        yield _run
    finally:
        _drop_all(server_url)


def _password_hashes(database_url) -> dict[str, str]:
    with psycopg.connect(database_url) as admin:
        rows = admin.execute(
            "SELECT rolname, rolpassword FROM pg_authid WHERE rolname = ANY(%s)", (NAMES,)
        ).fetchall()
    return dict(rows)


def _login(url):
    return psycopg.connect(url, autocommit=False)


def _can(url, statement) -> bool:
    with _login(url) as conn:
        try:
            conn.execute(statement)
        except psycopg.errors.InsufficientPrivilege:
            return False
        finally:
            conn.rollback()
    return True


def test_every_login_is_created_and_can_connect(run):
    code, lines = run()
    assert code == 0 and list(lines) == NAMES
    for name, url in lines.items():
        with _login(url) as conn:
            assert conn.execute("SELECT current_user").fetchone()[0] == name


def test_each_login_has_one_group_and_no_privileges_of_its_own(run, database_url):
    run()
    with psycopg.connect(database_url) as admin:
        for name in NAMES:
            flags = admin.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, rolinherit "
                "FROM pg_roles WHERE rolname = %s",
                (name,),
            ).fetchone()
            assert flags == (False, False, False, True, True)
            groups = admin.execute(
                "SELECT g.rolname FROM pg_auth_members m"
                " JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles r ON r.oid = m.member"
                " WHERE r.rolname = %s",
                (name,),
            ).fetchall()
            assert [g[0] for g in groups] == [GROUP_OF[name]]


def test_the_owner_read_login_reads_but_cannot_write(run):
    _, lines = run()
    url = lines["ta_owner_read_login"]
    for table in (
        "decisions",
        "risk_verdicts",
        "reports",
        "account_snapshots",
        "execution_refusals",
        "orchestrator_runs",
    ):
        assert _can(url, f"SELECT count(*) FROM {table}"), table
    assert not _can(url, "INSERT INTO decisions DEFAULT VALUES")
    assert not _can(url, "UPDATE system_state SET trading_paused = true")


def test_the_owner_control_login_sets_the_pause_and_nothing_else(run):
    _, lines = run()
    url = lines["ta_owner_control_login"]
    assert _can(url, "UPDATE system_state SET trading_paused = true, updated_at = now()")
    assert not _can(url, "UPDATE system_state SET halt_triggered_on = NULL")
    assert not _can(url, "INSERT INTO decisions DEFAULT VALUES")


def test_the_risk_gate_login_cannot_read_orders(run):
    _, lines = run()
    assert not _can(lines["ta_risk_gate_login"], "SELECT 1 FROM orders")
    assert _can(lines["ta_risk_gate_login"], "SELECT 1 FROM decisions")


def test_every_login_can_read_what_its_group_reads_and_no_more(run):
    """Through the group's grants (INHERIT): each login's table SELECT matches the matrix."""
    _, lines = run()
    for name, url in lines.items():
        group = GROUP_OF[name]
        for table, roles in GRANTS.items():
            ops = roles.get(group, set())
            if any(op.startswith("S:") for op in ops):
                continue  # column-level reads are the matrix test's job
            expected = "S" in ops
            assert _can(url, f"SELECT 1 FROM {table} LIMIT 0") is expected, (name, table)


def test_a_second_run_changes_no_password(run, database_url):
    run()
    before = _password_hashes(database_url)
    code, lines = run()
    assert code == 0
    assert set(lines.values()) == {"exists, unchanged"} and list(lines) == NAMES
    assert _password_hashes(database_url) == before


def test_reset_changes_only_the_named_login(run, database_url):
    _, first = run()
    before = _password_hashes(database_url)
    code, lines = run("--reset", "ta_research_login")
    after = _password_hashes(database_url)
    assert code == 0
    assert lines["ta_research_login"].startswith("postgresql://ta_research_login:")
    assert lines["ta_research_login"] != first["ta_research_login"]
    assert [n for n in NAMES if after[n] != before[n]] == ["ta_research_login"]
    with _login(lines["ta_research_login"]) as conn:
        conn.execute("SELECT 1")
    with pytest.raises(psycopg.OperationalError):
        _login(first["ta_research_login"])


def test_reset_of_a_missing_login_exits_2_and_creates_nothing(run, database_url):
    code, lines = run("--reset", "ta_research_login")
    assert code == 2 and lines == {}
    assert _password_hashes(database_url) == {}


def test_a_missing_group_role_exits_2_and_creates_nothing(run, database_url, monkeypatch):
    # Group roles are cluster-wide and shared with every other test, so one is
    # swapped for a name that doesn't exist rather than dropped.
    broken = (*LOGINS[:-1], logins.Login("ta_owner_control_login", "ta_no_such_group_xyz"))
    monkeypatch.setattr(logins, "LOGINS", broken)
    code, lines = run()
    assert code == 2 and lines == {}
    assert _password_hashes(database_url) == {}


@pytest.mark.parametrize(
    "alteration",
    ["CREATEDB", "CREATEROLE", "SUPERUSER"],
)
def test_an_existing_login_with_extra_privileges_is_refused_and_nothing_changes(
    run, database_url, alteration
):
    run()
    before = _password_hashes(database_url)
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"ALTER ROLE ta_research_login {alteration}")
    code, lines = run("--reset", "ta_research_login")
    assert code == 2 and lines == {}
    assert _password_hashes(database_url) == before


def test_an_existing_login_in_a_second_group_is_refused(run, database_url):
    run()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("GRANT ta_execution TO ta_research_login")
    assert run()[0] == 2


def test_the_stored_password_is_a_scram_verifier(run, database_url):
    run()
    assert all(v.startswith("SCRAM-SHA-256$") for v in _password_hashes(database_url).values())
