from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0016_adds_persisted_ui_preferences(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0016.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "0015_execution_lease")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO admin_accounts "
            "(id, singleton_slot, username, password_hash, created_at, password_changed_at) "
            "VALUES ('admin-1', 1, 'admin', 'hash', '2026-08-01 00:00:00', "
            "'2026-08-01 00:00:00')"
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        interval, updated_at = connection.execute(
            "SELECT timetable_refresh_interval_seconds, preferences_updated_at "
            "FROM admin_accounts WHERE id = 'admin-1'"
        ).fetchone()
        assert interval == 5
        assert updated_at is not None
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE admin_accounts SET timetable_refresh_interval_seconds = 4 "
                "WHERE id = 'admin-1'"
            )

    command.downgrade(config, "0015_execution_lease")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(admin_accounts)")
        }
        assert "timetable_refresh_interval_seconds" not in columns
        assert "preferences_updated_at" not in columns

    get_settings.cache_clear()
