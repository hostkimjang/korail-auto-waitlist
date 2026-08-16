from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0011_korail_browser_snapshots_round_trip(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0011.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "korail_browser_snapshot_batches" in tables
        assert "korail_browser_seat_snapshots" in tables
        batch_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'korail_browser_snapshot_batches'"
        ).fetchone()[0]
        snapshot_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'korail_browser_seat_snapshots'"
        ).fetchone()[0]
        evidence_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'timetable_seat_evidence'"
        ).fetchone()[0]
        assert "source = 'korail-official-browser-companion'" in batch_sql
        assert "fresh_until > observed_at" in batch_sql
        assert (
            "'AVAILABLE', 'LIMITED', 'STANDING_PLUS_SEAT', 'STANDING_ONLY', 'SOLD_OUT'"
            in snapshot_sql
        )
        assert "official_page_browser_companion" in evidence_sql
        assert "source = 'korail-official-browser-companion'" in evidence_sql
        unique_columns = []
        for _, index_name, is_unique, *_ in connection.execute(
            "PRAGMA index_list('korail_browser_seat_snapshots')"
        ):
            if is_unique:
                unique_columns.append(
                    [row[2] for row in connection.execute(f"PRAGMA index_info('{index_name}')")]
                )
        assert ["batch_id", "train_number", "seat_class"] in unique_columns

    command.downgrade(config, "0010_timetable_seat_evidence")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "korail_browser_snapshot_batches" not in tables
        assert "korail_browser_seat_snapshots" not in tables

    get_settings.cache_clear()
