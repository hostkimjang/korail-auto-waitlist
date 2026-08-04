from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0025_adds_admin_observation_interval_preferences(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "migration-0025.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_revision(
        "0025_admin_observation_intervals"
    ) is not None
    command.upgrade(config, "0025_admin_observation_intervals")

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(admin_accounts)").fetchall()
        }
        assert columns["balanced_observation_interval_seconds"][4] == "'600'"
        assert columns["focused_observation_interval_seconds"][4] == "'25'"
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'admin_accounts'"
        ).fetchone()[0]
        assert "balanced_observation_interval_seconds BETWEEN 30 AND 600" in table_sql
        assert "focused_observation_interval_seconds BETWEEN 20 AND 30" in table_sql

    command.downgrade(config, "0024_watch_observation_speed")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(admin_accounts)").fetchall()
        }
        assert "balanced_observation_interval_seconds" not in columns
        assert "focused_observation_interval_seconds" not in columns

    get_settings.cache_clear()
