from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0028_adds_unique_web_push_device_key(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0028.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == ("0034_progress_terminal_time")

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: (row[2], bool(row[3]))
            for row in connection.execute("PRAGMA table_info(notification_channels)").fetchall()
        }
        indexes = {
            row[1]: bool(row[2])
            for row in connection.execute("PRAGMA index_list(notification_channels)").fetchall()
            if row[1].startswith("ix_")
        }
        assert columns["web_push_device_key"] == ("VARCHAR(43)", False)
        assert indexes["ix_notification_channels_web_push_device_key"] is True

    command.downgrade(config, "0027_native_push_pairing")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(notification_channels)").fetchall()
        }
        assert "web_push_device_key" not in columns

    get_settings.cache_clear()
