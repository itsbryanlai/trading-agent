"""The database's privileges equal contracts/role-grants.md — no more, no less."""

from __future__ import annotations

import pytest

from tests.integration.helpers import attempt
from tests.integration.storage.factories import OBJECTS, ops_for, probe
from tests.integration.storage.grants_matrix import GRANTS, ROLES

CASES = [(name, role, op) for name in sorted(OBJECTS) for role in ROLES for op in ops_for(name)]


def test_matrix_and_objects_describe_the_same_objects():
    assert set(GRANTS) == set(OBJECTS)
    for name, by_role in GRANTS.items():
        assert set(by_role) <= set(ROLES), f"unknown role in GRANTS[{name!r}]"
        for role, ops in by_role.items():
            unknown = set(ops) - set(ops_for(name))
            assert not unknown, f"GRANTS[{name!r}][{role!r}] has unprobeable ops {unknown}"


@pytest.mark.parametrize(("name", "role", "op"), CASES)
def test_privilege_matches_contract(conn, name, role, op):
    granted = GRANTS[name].get(role, set())
    # A table-level grant covers every column of that kind (S covers S:<col>);
    # the catalog test below still demands the matrix list exactly what's granted.
    covered = op in granted or (op[:2] in ("S:", "U:") and op[0] in granted)
    expected = "allowed" if covered else "denied"
    assert attempt(conn, role, probe(name, op)) == expected


_CATALOG_PRIVILEGES = """
    SELECT c.relname AS object, r.rolname AS role, a.privilege_type AS privilege,
           NULL::name AS column_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
    CROSS JOIN LATERAL aclexplode(c.relacl) a
    JOIN pg_roles r ON r.oid = a.grantee
    WHERE r.rolname LIKE 'ta\\_%'
    UNION ALL
    SELECT c.relname, r.rolname, a.privilege_type, att.attname
    FROM pg_attribute att
    JOIN pg_class c ON c.oid = att.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
    CROSS JOIN LATERAL aclexplode(att.attacl) a
    JOIN pg_roles r ON r.oid = a.grantee
    WHERE r.rolname LIKE 'ta\\_%'
"""

_TABLE_LEVEL = {"SELECT": "S", "INSERT": "I", "UPDATE": "U", "DELETE": "D"}


def _as_op(privilege: str, column: str | None) -> str:
    if column is None:
        return _TABLE_LEVEL.get(privilege, privilege)  # TRUNCATE etc. never match
    return f"{_TABLE_LEVEL.get(privilege, privilege)}:{column}"


def test_catalog_privileges_equal_contract(conn):
    """Read from pg_class/pg_attribute ACLs, not information_schema: those views
    filter by the querying role's memberships and can come back empty, which
    would let this test pass without checking anything."""
    rows = conn.execute(_CATALOG_PRIVILEGES).fetchall()
    actual = {(r["object"], r["role"], _as_op(r["privilege"], r["column_name"])) for r in rows}
    expected = {
        (name, role, op)
        for name, by_role in GRANTS.items()
        for role, ops in by_role.items()
        for op in ops
    }
    if expected:
        assert rows, "catalog query returned no ta_* privileges at all"
    assert actual - expected == set(), "granted in the database but not in the contract"
    assert expected - actual == set(), "in the contract but not granted in the database"
