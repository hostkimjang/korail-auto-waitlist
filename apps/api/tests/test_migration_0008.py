from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_INDEXES = {
    "ix_seat_observations_observed_at": "seat_observations",
    "ix_reservation_attempts_started_at": "reservation_attempts",
    "ix_watch_transition_history_created_at": "watch_transition_history",
    "ix_outbox_events_processed_at": "outbox_events",
}


def test_migration_0008_operations_indexes_round_trip(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0008.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "0007_station_catalog_cache")
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
        indexes = {name: table for name, table in rows}
        for name, table in EXPECTED_INDEXES.items():
            assert indexes[name] == table

    command.downgrade(config, "0007_station_catalog_cache")
    with sqlite3.connect(database_path) as connection:
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert EXPECTED_INDEXES.keys().isdisjoint(index_names)

    get_settings.cache_clear()
