from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def _schema_version_default(connection: sqlite3.Connection) -> str | None:
    row = next(
        row
        for row in connection.execute("PRAGMA table_info(station_catalog_cache)").fetchall()
        if row[1] == "schema_version"
    )
    value = row[4]
    return None if value is None else str(value).strip("'\"")


def test_migration_0041_updates_only_the_station_cache_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration-0041.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == "0041_station_cache_v4"
    assert scripts.get_revision("0041_station_cache_v4") is not None

    command.upgrade(config, "0040_legacy_failed_unknown")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE station_catalog_cache SET schema_version = 3 "
            "WHERE cache_key = 'tago_station_catalog_all'"
        )
        connection.commit()
        assert _schema_version_default(connection) == "2"

    command.upgrade(config, "0041_station_cache_v4")
    with sqlite3.connect(database_path) as connection:
        assert _schema_version_default(connection) == "4"
        assert connection.execute(
            "SELECT schema_version FROM station_catalog_cache "
            "WHERE cache_key = 'tago_station_catalog_all'"
        ).fetchone() == (3,)

    command.downgrade(config, "0040_legacy_failed_unknown")
    with sqlite3.connect(database_path) as connection:
        assert _schema_version_default(connection) == "2"
        assert connection.execute(
            "SELECT schema_version FROM station_catalog_cache "
            "WHERE cache_key = 'tago_station_catalog_all'"
        ).fetchone() == (3,)
