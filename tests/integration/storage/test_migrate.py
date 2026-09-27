import psycopg

from trading_agent.storage.migrate import apply_migrations, discover


def test_applies_every_migration_once_then_nothing(make_database):
    url = make_database()
    versions = [m.version for m in discover()]

    assert apply_migrations(url) == versions
    assert apply_migrations(url) == []

    with psycopg.connect(url) as connection:
        recorded = [
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
    assert recorded == versions
