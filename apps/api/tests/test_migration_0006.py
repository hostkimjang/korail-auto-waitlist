from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0006_invalidates_legacy_sessions_and_round_trips(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0006.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "0005_persistence_foundation")
    now = datetime.now(UTC)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO admin_sessions (
                id, token_hash, csrf_hash, expires_at, revoked_at, created_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-passkey-session",
                "a" * 64,
                "b" * 64,
                (now + timedelta(hours=1)).isoformat(),
                None,
                now.isoformat(),
                now.isoformat(),
            ),
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "admin_accounts" in tables
        assert {"admin_credentials", "recovery_codes", "auth_challenges"}.issubset(tables)
        assert connection.execute("SELECT count(*) FROM admin_sessions").fetchone() == (0,)
        connection.execute(
            """
            INSERT INTO admin_accounts (
                id, singleton_slot, username, password_hash,
                created_at, password_changed_at, last_login_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "admin-one",
                1,
                "admin",
                "$argon2id$test",
                now.isoformat(),
                now.isoformat(),
                None,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO admin_accounts (
                    id, singleton_slot, username, password_hash,
                    created_at, password_changed_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "admin-two",
                    1,
                    "other",
                    "$argon2id$test",
                    now.isoformat(),
                    now.isoformat(),
                    None,
                ),
            )

    command.downgrade(config, "0005_persistence_foundation")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "admin_accounts" not in tables
        assert "admin_sessions" in tables
        assert connection.execute("SELECT count(*) FROM admin_sessions").fetchone() == (0,)

    get_settings.cache_clear()
