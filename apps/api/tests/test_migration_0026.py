from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0026_unifies_existing_observation_preferences_at_five_seconds(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "migration-0026.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == (
        "0026_unified_observation"
    )
    command.upgrade(config, "0025_admin_observation_intervals")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO admin_accounts "
            "(id, singleton_slot, username, password_hash, "
            "balanced_observation_interval_seconds, focused_observation_interval_seconds, "
            "preferences_updated_at, created_at, password_changed_at) "
            "VALUES ('admin', 1, 'admin', 'hash', 120, 20, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(admin_accounts)").fetchall()
        }
        assert columns["observation_interval_seconds"][4] == "'5'"
        assert "balanced_observation_interval_seconds" not in columns
        assert "focused_observation_interval_seconds" not in columns
        assert connection.execute(
            "SELECT observation_interval_seconds FROM admin_accounts WHERE id = 'admin'"
        ).fetchone() == (5,)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'admin_accounts'"
        ).fetchone()[0]
        assert "observation_interval_seconds BETWEEN 1 AND 600" in table_sql

    command.downgrade(config, "0025_admin_observation_intervals")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(admin_accounts)").fetchall()
        }
        assert "observation_interval_seconds" not in columns
        assert columns["balanced_observation_interval_seconds"][4] == "'600'"
        assert columns["focused_observation_interval_seconds"][4] == "'25'"

    get_settings.cache_clear()
