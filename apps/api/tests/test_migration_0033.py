from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0033_adds_expiring_watch_observation_claim(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0033.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == "0041_station_cache_v4"

    command.upgrade(config, "0032_manual_reservation_rearm")
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: (row[2], bool(row[3]), row[4])
            for row in connection.execute("PRAGMA table_info(watches)")
        }
        assert columns["observation_in_flight_until"] == ("DATETIME", False, None)

    command.downgrade(config, "0032_manual_reservation_rearm")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(watches)")}
        assert "observation_in_flight_until" not in columns

    get_settings.cache_clear()
