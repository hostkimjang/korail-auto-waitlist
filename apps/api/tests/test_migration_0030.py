from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0030_persists_reservation_progress(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0030.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == ("0034_progress_terminal_time")

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: (row[2], bool(row[3]), row[4])
            for row in connection.execute("PRAGMA table_info(reservation_attempts)").fetchall()
        }
        assert columns["progress_stages"] == ("JSON", True, "'[]'")

    command.downgrade(config, "0029_ui_refresh_interval")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(reservation_attempts)").fetchall()
        }
        assert "progress_stages" not in columns

    get_settings.cache_clear()
