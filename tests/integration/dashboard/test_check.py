"""`--check` against real logins (specs/014 tasks T025; analyze S6).

Logins are cluster-wide, so each test creates the exact production names and drops them after,
even on failure. The throwaway server is the only place this runs.
"""

from __future__ import annotations

import secrets

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from trading_agent.dashboard import auth, check
from trading_agent.dashboard.control import ControlWriter
from trading_agent.dashboard.deployed import DEFAULT_CONFIG_DIR
from trading_agent.dashboard.store import PostgresStore

READ, CONTROL = "ta_dashboard_login", "ta_dashboard_control_login"
GOOD_HASH = auth.hash_password("correct horse battery staple", salt=b"0123456789abcdef")
SECRET = "fake-secret-not-real-0123456789abcdef0123"


@pytest.fixture
def world(database_url, server_url):
    """A way to make a login and the URL that connects as it; all are dropped afterwards."""
    made: list[str] = []
    admin = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)

    def make(name: str, *groups: str, options: str = "") -> str:
        password = secrets.token_hex(8)
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} " + options).format(
                sql.Identifier(name), sql.Literal(password)
            )
        )
        made.append(name)
        for group in groups:
            admin.execute(
                sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(name))
            )
        return make_conninfo(database_url, user=name, password=password)

    yield make
    admin.close()
    with psycopg.connect(server_url, autocommit=True) as server:
        for name in made:
            server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


def run(read_url: str, control_url: str):
    return check.run(
        store=PostgresStore(read_url, 10),
        control=ControlWriter(control_url),
        password_hash=GOOD_HASH,
        secret=SECRET,
        config_dir=DEFAULT_CONFIG_DIR,
    )


def lines(results) -> dict[str, str]:
    return {r.name: r.line() for r in results}


def test_the_two_exact_logins_in_their_one_group_each_pass_everything(world):
    read = world(READ, "ta_dashboard")
    control = world(CONTROL, "ta_dashboard_control")
    got = lines(run(read, control))
    assert set(got.values()) == {f"{name}: ok" for name in got}
    assert list(got) == [
        "database_read",
        "database_control",
        "password_hash",
        "session_secret",
        "schedule",
        "config",
    ]


def test_a_read_login_also_in_the_control_group_fails_extra_role(world):
    read = world(READ, "ta_dashboard", "ta_dashboard_control")
    control = world(CONTROL, "ta_dashboard_control")
    got = lines(run(read, control))
    assert got["database_read"] == "database_read: FAILED extra_role:ta_dashboard_control"
    assert got["database_control"] == "database_control: ok"


def test_another_name_in_the_read_group_fails_wrong_login(world):
    owner_read = world("ta_owner_read_login", "ta_dashboard")  # as the owner's login is
    control = world(CONTROL, "ta_dashboard_control")
    got = lines(run(owner_read, control))
    assert got["database_read"] == "database_read: FAILED wrong_login"


def test_the_owners_control_login_in_the_control_slot_fails_wrong_login(world):
    read = world(READ, "ta_dashboard")
    owner_control = world("ta_owner_control_login", "ta_dashboard_control")
    got = lines(run(read, owner_control))
    assert got["database_control"] == "database_control: FAILED wrong_login"


@pytest.mark.parametrize("attribute", ["CREATEROLE", "CREATEDB", "SUPERUSER", "BYPASSRLS"])
def test_a_privileged_login_fails_privileged(world, attribute):
    read = world(READ, "ta_dashboard", options=attribute)
    control = world(CONTROL, "ta_dashboard_control")
    got = lines(run(read, control))
    assert got["database_read"] == "database_read: FAILED privileged"


def test_a_login_in_no_dashboard_group_fails_wrong_role(world):
    read = world(READ, "ta_assistant")
    control = world(CONTROL, "ta_dashboard_control")
    got = lines(run(read, control))
    assert got["database_read"] == "database_read: FAILED wrong_role:ta_dashboard"


def test_a_group_role_membership_through_another_group_counts_as_extra(world, database_url):
    # pg_has_role(..., 'MEMBER') follows inheritance, so a membership reached through another
    # group is still caught.
    read = world(READ, "ta_dashboard")
    control = world(CONTROL, "ta_dashboard_control")
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("GRANT ta_assistant TO ta_dashboard_control")
        try:
            got = lines(run(read, control))
        finally:
            admin.execute("REVOKE ta_assistant FROM ta_dashboard_control")
    assert got["database_control"] == "database_control: FAILED extra_role:ta_assistant"


def test_a_wrong_password_is_unreachable_with_no_message(world):
    read = world(READ, "ta_dashboard")
    control = world(CONTROL, "ta_dashboard_control")
    bad = make_conninfo(read, password="wrong-" + secrets.token_hex(3))
    got = lines(run(bad, control))
    assert got["database_read"] == "database_read: FAILED unreachable"


def test_checking_writes_nothing(world, database_url):
    read = world(READ, "ta_dashboard")
    control = world(CONTROL, "ta_dashboard_control")
    with psycopg.connect(database_url, row_factory=dict_row) as admin:
        before = admin.execute("SELECT * FROM system_state").fetchone()
        run(read, control)
        assert admin.execute("SELECT * FROM system_state").fetchone() == before
