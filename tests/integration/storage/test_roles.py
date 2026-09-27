import pytest

from tests.integration.storage.grants_matrix import ROLES

ALL_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"


@pytest.mark.parametrize("role", ROLES)
def test_role_exists_and_cannot_log_in(conn, role):
    row = conn.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    assert row is not None, f"{role} missing"
    assert row["rolcanlogin"] is False


@pytest.mark.parametrize("role", ROLES)
def test_role_cannot_create_in_public_schema(conn, role):
    row = conn.execute(
        "SELECT has_schema_privilege(%s, 'public', 'CREATE') AS can", (role,)
    ).fetchone()
    assert row["can"] is False


@pytest.mark.parametrize("role", ROLES)
def test_role_has_no_access_to_schema_migrations(conn, role):
    row = conn.execute(
        "SELECT has_table_privilege(%s, 'schema_migrations', %s) AS can",
        (role, ALL_TABLE_PRIVILEGES),
    ).fetchone()
    assert row["can"] is False
