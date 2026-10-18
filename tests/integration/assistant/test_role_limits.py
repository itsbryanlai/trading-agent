"""The Assistant's login can read the menu and write nothing (specs/013 T026; research A21,
ADR 0023).

`ta_assistant_login` is a member of `ta_assistant` only, and `ta_assistant` holds SELECT and
nothing else (role-grants.md). The test makes its own login, as `ta_journal`'s does, and checks
both the login and the group role: every write denied on every table in the grant matrix,
`system_state` included, and every menu entry's SQL running.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tests.integration.helpers import as_role, attempt
from tests.integration.storage.factories import OBJECTS, probe
from tests.integration.storage.grants_matrix import GRANTS
from trading_agent.assistant.store import KNOWN_AMONG, KNOWN_SYMBOLS, PLANS

LOGIN = "ta_assistant_probe_login"
TABLES = sorted(name for name, spec in OBJECTS.items() if spec.kind == "table")
# One write probe per table and kind, including each column-level UPDATE the table has.
WRITES = [
    (table, op)
    for table in TABLES
    for op in ("I", "U", "D") + tuple(f"U:{c}" for c in OBJECTS[table].columns_for_update)
]
MENU_STATEMENTS = [
    (name, tag, statement) for name, parts in PLANS.items() for tag, statement in parts
] + [
    ("known_symbols", None, KNOWN_SYMBOLS),
    ("known_among", None, KNOWN_AMONG),
]


@pytest.fixture(params=["group role", "login"])
def assistant_role(request, conn):
    """`ta_assistant` itself, and a login made here that is a member of `ta_assistant` only,
    as `ta_assistant_login` is (storage/logins.py). Both are rolled back with the test."""
    if request.param == "group role":
        return "ta_assistant"
    conn.execute(
        f"CREATE ROLE {LOGIN} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
    )
    conn.execute(f"GRANT ta_assistant TO {LOGIN}")
    groups = conn.execute(
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
        "JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = %s",
        (LOGIN,),
    ).fetchall()
    assert [g["rolname"] for g in groups] == ["ta_assistant"]
    return LOGIN


def _params() -> dict:
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    return {
        "symbol": "",
        "symbols": ["MU"],
        "agent": "",
        "limit": 51,
        "start": now - timedelta(days=1),
        "end": now,
        "first_day": date(2026, 10, 19),
        "last_day": date(2026, 10, 20),
    }


def test_the_matrix_lists_every_table_the_probes_cover():
    assert set(TABLES) <= set(GRANTS)
    assert "system_state" in TABLES and len(TABLES) >= 10


@pytest.mark.parametrize(("table", "op"), WRITES)
def test_the_assistant_role_can_not_write_any_table(conn, assistant_role, table, op):
    assert attempt(conn, assistant_role, probe(table, op)) == "denied"


@pytest.mark.parametrize("table", TABLES)
def test_the_assistant_role_can_read_every_table_it_has_a_grant_on(conn, assistant_role, table):
    expected = "allowed" if "S" in GRANTS[table].get("ta_assistant", set()) else "denied"
    assert attempt(conn, assistant_role, probe(table, "S")) == expected


@pytest.mark.parametrize(
    ("name", "tag", "statement"),
    MENU_STATEMENTS,
    ids=[f"{n}:{t or 'only'}" for n, t, _ in MENU_STATEMENTS],
)
def test_every_menu_statement_runs_as_the_assistant_role(
    conn, assistant_role, name, tag, statement
):
    with as_role(conn, assistant_role):
        conn.execute(statement, _params()).fetchall()
