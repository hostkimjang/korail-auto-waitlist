from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0013_adds_standing_status_and_downgrades_existing_rows(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0013.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert len(ScriptDirectory.from_config(config).get_current_head()) <= 32

    command.upgrade(config, "0012_browser_companion_pairing")
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        snapshot_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'korail_browser_seat_snapshots'"
        ).fetchone()[0]
        assert "STANDING_PLUS_SEAT" in snapshot_sql
        connection.execute(
            "INSERT INTO korail_browser_snapshot_batches "
            "(id, origin, destination, travel_date, passenger_count, source, "
            "observed_at, fresh_until, created_at) VALUES "
            "('batch', '대전', '서울', '2030-07-30', 1, "
            "'korail-official-browser-companion', "
            "'2030-07-29 00:00:00', '2030-07-29 00:02:00', '2030-07-29 00:00:00')"
        )
        connection.execute(
            "INSERT INTO korail_browser_seat_snapshots "
            "(id, batch_id, train_number, departure_at, seat_class, status, created_at) "
            "VALUES ('snapshot', 'batch', '26', '2030-07-30 03:00:00', "
            "'STANDARD', 'STANDING_PLUS_SEAT', '2030-07-29 00:00:00')"
        )
        connection.commit()

    command.downgrade(config, "0012_browser_companion_pairing")
    with sqlite3.connect(database_path) as connection:
        status = connection.execute(
            "SELECT status FROM korail_browser_seat_snapshots WHERE id = 'snapshot'"
        ).fetchone()[0]
        snapshot_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'korail_browser_seat_snapshots'"
        ).fetchone()[0]
        assert status == "AVAILABLE"
        assert "STANDING_PLUS_SEAT" not in snapshot_sql

    get_settings.cache_clear()
