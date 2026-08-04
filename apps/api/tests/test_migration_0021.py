from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0021_adds_bounded_reconciliation_schedule(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0021.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_revision("0021_bounded_reconcile") is not None
    command.upgrade(config, "0021_bounded_reconcile")

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(reservation_attempts)")
        }
        assert columns["reconciliation_attempt_count"][3] == 1
        assert columns["reconciliation_attempt_count"][4] == "'0'"
        assert "next_reconcile_at" in columns
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'reservation_attempts'"
        ).fetchone()[0]
        assert "reconciliation_attempt_count >= 0" in table_sql
        assert "reconciliation_attempt_count <= 3" in table_sql

    get_settings.cache_clear()
