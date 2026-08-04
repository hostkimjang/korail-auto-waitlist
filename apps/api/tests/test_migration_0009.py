from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0009_official_page_confirmations_round_trip(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0009.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('official_page_seat_confirmations')")
        }
        assert {
            "provider",
            "train_number",
            "batch_id",
            "passenger_count",
            "seat_class",
            "status",
            "observed_at",
        } <= columns
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list('official_page_seat_confirmations')")
        }
        assert "ix_official_page_confirmation_route_fresh" in indexes
        assert "ix_official_page_confirmation_batch_id" in indexes
        unique_column_sets = []
        for _, index_name, is_unique, *_ in connection.execute(
            "PRAGMA index_list('official_page_seat_confirmations')"
        ):
            if is_unique:
                unique_column_sets.append(
                    [row[2] for row in connection.execute(f"PRAGMA index_info('{index_name}')")]
                )
        assert [
            "batch_id",
            "seat_class",
        ] in unique_column_sets
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'official_page_seat_confirmations'"
        ).fetchone()[0]
        assert "'AVAILABLE', 'SOLD_OUT', 'WAITLIST_AVAILABLE', 'NOT_OFFERED'" in table_sql
        assert "fresh_until > observed_at" in table_sql
        assert "updated_at" not in columns

    command.downgrade(config, "0008_operations_indexes")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "official_page_seat_confirmations" not in tables

    get_settings.cache_clear()
