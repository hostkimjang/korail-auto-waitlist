from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0022_adds_one_shot_post_deadline_marker(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0022.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_revision("0022_post_deadline_check") is not None
    command.upgrade(config, "0022_post_deadline_check")

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(reservation_attempts)")
        }
        assert "post_deadline_reconciled_at" in columns
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(reservation_attempts)")
        }
        assert "ix_reservation_attempts_post_deadline_reconciled_at" in indexes

    get_settings.cache_clear()
