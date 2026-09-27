from pathlib import Path

import pytest

from trading_agent.storage.migrate import MIGRATIONS_DIR, MigrationError, discover


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_text("SELECT 1;")


def test_returned_in_version_order_regardless_of_creation_order(tmp_path):
    _touch(tmp_path, "0003_c.sql", "0001_a.sql", "0002_b.sql")
    assert [m.version for m in discover(tmp_path)] == ["0001", "0002", "0003"]


def test_duplicate_version_raises(tmp_path):
    _touch(tmp_path, "0001_a.sql", "0001_b.sql")
    with pytest.raises(MigrationError, match="duplicate"):
        discover(tmp_path)


@pytest.mark.parametrize("name", ["1_short.sql", "0001-dash.sql", "0001_Upper.sql", "abcd_x.sql"])
def test_malformed_sql_filename_raises(tmp_path, name):
    _touch(tmp_path, name)
    with pytest.raises(MigrationError, match="does not match"):
        discover(tmp_path)


def test_non_sql_files_ignored(tmp_path):
    _touch(tmp_path, "0001_a.sql", "README.md", "notes.txt")
    assert [m.path.name for m in discover(tmp_path)] == ["0001_a.sql"]


def test_shipped_migrations_are_well_formed():
    assert discover(MIGRATIONS_DIR), "no migrations shipped"
