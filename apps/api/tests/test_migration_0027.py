from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0027_adds_native_pairing_and_credential_tables(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0027.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_revision("0027_native_push_pairing") is not None

    command.upgrade(config, "0027_native_push_pairing")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"native_push_pairings", "native_push_credentials"} <= tables
        pairing_columns = {
            row[1]: (row[2], bool(row[3]), bool(row[5]))
            for row in connection.execute("PRAGMA table_info(native_push_pairings)").fetchall()
        }
        credential_columns = {
            row[1]: (row[2], bool(row[3]), bool(row[5]))
            for row in connection.execute("PRAGMA table_info(native_push_credentials)").fetchall()
        }
        assert pairing_columns == {
            "id": ("VARCHAR(36)", True, True),
            "code_hash": ("VARCHAR(64)", True, False),
            "kind": ("VARCHAR(15)", True, False),
            "expires_at": ("DATETIME", True, False),
            "consumed_at": ("DATETIME", False, False),
            "created_at": ("DATETIME", True, False),
        }
        assert credential_columns == {
            "id": ("VARCHAR(36)", True, True),
            "token_hash": ("VARCHAR(64)", True, False),
            "channel_id": ("VARCHAR(36)", True, False),
            "kind": ("VARCHAR(15)", True, False),
            "last_used_at": ("DATETIME", False, False),
            "revoked_at": ("DATETIME", False, False),
            "created_at": ("DATETIME", True, False),
        }
        credential_indexes = {
            row[1]: bool(row[2])
            for row in connection.execute("PRAGMA index_list(native_push_credentials)").fetchall()
            if row[1].startswith("ix_")
        }
        pairing_indexes = {
            row[1]: bool(row[2])
            for row in connection.execute("PRAGMA index_list(native_push_pairings)").fetchall()
            if row[1].startswith("ix_")
        }
        assert pairing_indexes == {
            "ix_native_push_pairings_code_hash": True,
            "ix_native_push_pairings_expires_at": False,
        }
        assert credential_indexes == {
            "ix_native_push_credentials_token_hash": True,
            "ix_native_push_credentials_channel_id": True,
            "ix_native_push_credentials_revoked_at": False,
        }
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(native_push_credentials)"
        ).fetchall()
        assert [(row[2], row[3], row[4], row[6]) for row in foreign_keys] == [
            ("notification_channels", "channel_id", "id", "RESTRICT")
        ]

    command.downgrade(config, "0026_unified_observation")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "native_push_pairings" not in tables
        assert "native_push_credentials" not in tables

    get_settings.cache_clear()
