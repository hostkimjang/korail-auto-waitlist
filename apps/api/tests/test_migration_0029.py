from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0029_allows_one_second_ui_refresh(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0029.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == ("0034_progress_terminal_time")

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO admin_accounts "
            "(id, singleton_slot, username, password_hash, timetable_refresh_interval_seconds, "
            "observation_interval_seconds, preferences_updated_at, created_at, "
            "password_changed_at) "
            "VALUES ('admin', 1, 'admin', 'hash', 1, 5, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE admin_accounts SET timetable_refresh_interval_seconds = 0 "
                "WHERE id = 'admin'"
            )

    command.downgrade(config, "0028_web_push_device_key")
    with sqlite3.connect(database_path) as connection:
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'admin_accounts'"
        ).fetchone()[0]
        assert "timetable_refresh_interval_seconds BETWEEN 5 AND 300" in table_sql
        refresh_interval = connection.execute(
            "SELECT timetable_refresh_interval_seconds FROM admin_accounts WHERE id = 'admin'"
        ).fetchone()[0]
        assert refresh_interval == 5

    get_settings.cache_clear()
