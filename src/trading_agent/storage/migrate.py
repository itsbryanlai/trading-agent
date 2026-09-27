"""Forward-only migration runner.

Run as a deploy step with the admin credential, never on component startup:
every component connects as a role that cannot change the schema or its own
permissions (specs/001-data-model/research.md R2).

    python -m trading_agent.storage.migrate
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from trading_agent.storage.db import ConfigError, connect, require_env

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: dict[str, Migration] = {}
    for path in directory.iterdir():
        if path.suffix != ".sql":
            continue
        match = _FILENAME.match(path.name)
        if not match:
            raise MigrationError(f"migration filename does not match NNNN_name.sql: {path.name}")
        version = match.group(1)
        if version in migrations:
            raise MigrationError(
                f"duplicate migration version {version}: "
                f"{migrations[version].path.name} and {path.name}"
            )
        migrations[version] = Migration(version, path)
    return [migrations[v] for v in sorted(migrations)]


def apply_migrations(url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every migration not yet recorded. Returns the versions applied."""
    pending = discover(directory)
    with connect(url) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    done = {row["version"] for row in rows}

    applied: list[str] = []
    for migration in pending:
        if migration.version in done:
            continue
        with connect(url) as connection:
            connection.execute(migration.path.read_text())
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (migration.version,)
            )
        applied.append(migration.version)
    return applied


def main() -> int:
    try:
        url = require_env("ADMIN_DATABASE_URL")
        applied = apply_migrations(url)
    except (ConfigError, MigrationError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for version in applied:
        print(f"applied {version}")
    if not applied:
        print("up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
