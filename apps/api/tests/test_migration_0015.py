from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0015_provider_execution_lease_round_trip(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0015.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert len(ScriptDirectory.from_config(config).get_current_head()) <= 32

    command.upgrade(config, "0014_evidence_eligibility")
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'provider_execution_leases'"
        ).fetchone()[0]
        assert "PRIMARY KEY (provider, account_scope)" in table_sql
        assert "fencing_token >= 1" in table_sql
        assert "owner_token IS NULL AND expires_at IS NULL" in table_sql
        connection.execute(
            "INSERT INTO provider_execution_leases "
            "(provider, account_scope, owner_token, fencing_token, expires_at, updated_at) "
            "VALUES ('SRT', 'anonymous/public', 'replica-a', 1, "
            "'2030-07-30 00:01:00', '2030-07-30 00:00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO provider_execution_leases "
                "(provider, account_scope, owner_token, fencing_token, expires_at, updated_at) "
                "VALUES ('SRT', 'anonymous/public', 'replica-b', 2, "
                "'2030-07-30 00:02:00', '2030-07-30 00:00:00')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO provider_execution_leases "
                "(provider, account_scope, owner_token, fencing_token, expires_at, updated_at) "
                "VALUES ('KORAIL', 'bad-shape', NULL, 1, "
                "'2030-07-30 00:02:00', '2030-07-30 00:00:00')"
            )

    command.downgrade(config, "0014_evidence_eligibility")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "provider_execution_leases" not in tables

    get_settings.cache_clear()
