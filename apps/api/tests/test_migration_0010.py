from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0010_timetable_seat_evidence_round_trip(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0010.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info('timetable_seat_evidence')")
        }
        assert {
            "evidence_hash",
            "canonical_train_number",
            "provenance_kind",
            "registration_allowed",
            "registration_valid_until",
        } <= columns
        candidate_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('watch_candidates')")
        }
        assert "registration_evidence_id" in candidate_columns
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'timetable_seat_evidence'"
        ).fetchone()[0]
        assert "provider IN ('KORAIL', 'SRT')" in table_sql
        assert "provenance_kind = 'not_observed' AND status = 'UNKNOWN'" in table_sql
        assert "source = 'official-page-user-confirmation'" in table_sql
        assert "registration_valid_until > created_at" in table_sql

    command.downgrade(config, "0009_official_page_confirmations")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        candidate_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('watch_candidates')")
        }
        assert "timetable_seat_evidence" not in tables
        assert "registration_evidence_id" not in candidate_columns

    get_settings.cache_clear()
