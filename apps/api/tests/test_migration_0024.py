from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0024_adds_bounded_per_watch_observation_speed(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0024.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_revision(
        "0024_watch_observation_speed"
    ) is not None
    command.upgrade(config, "0024_watch_observation_speed")

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(watches)").fetchall()
        }
        assert columns["seat_observation_mode"][4] == "'BALANCED'"
        assert columns["focused_observation_interval_seconds"][4] == "'25'"
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'watches'"
        ).fetchone()[0]
        assert "seat_observation_mode IN ('BALANCED', 'FOCUSED')" in table_sql
        assert "focused_observation_interval_seconds BETWEEN 20 AND 30" in table_sql

    command.downgrade(config, "0023_extend_unknown_reconcile")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(watches)").fetchall()
        }
        assert "seat_observation_mode" not in columns
        assert "focused_observation_interval_seconds" not in columns

    get_settings.cache_clear()
