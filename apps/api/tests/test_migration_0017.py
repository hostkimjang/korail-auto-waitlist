from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0017_adds_provider_accounts_and_reservation_policy(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "migration-0017.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "0016_admin_ui_preferences")
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO watches (
                id, provider, origin, destination, travel_date, time_from, time_to,
                seat_class, passenger_count, train_numbers, notification_channel_ids,
                mode, status, dedupe_key, reservation_attempted, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "watch-before-0017",
                "MOCK",
                "서울",
                "부산",
                "2026-08-01",
                "09:00:00",
                "12:00:00",
                "standard",
                1,
                "[]",
                "[]",
                "official",
                "DRAFT",
                "before-0017",
                0,
                now,
                now,
            ),
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        watch_policy = connection.execute(
            "SELECT reservation_policy FROM watches WHERE id = 'watch-before-0017'"
        ).fetchone()
        assert watch_policy == ("NOTIFY_ONLY",)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE watches SET reservation_policy = 'PAY_AUTOMATICALLY' "
                "WHERE id = 'watch-before-0017'"
            )

        account_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(rail_provider_accounts)")
        }
        assert account_columns == {
            "id",
            "provider",
            "credentials_ciphertext",
            "enabled",
            "credential_version",
            "last_auth_status",
            "last_authenticated_at",
            "created_at",
            "updated_at",
        }
        connection.execute(
            "INSERT INTO rail_provider_accounts "
            "(id, provider, credentials_ciphertext) VALUES ('account-1', 'KORAIL', 'token')"
        )
        stored = connection.execute(
            "SELECT enabled, credential_version, last_auth_status "
            "FROM rail_provider_accounts WHERE id = 'account-1'"
        ).fetchone()
        assert stored == (1, 1, "not_checked")

        for statement in (
            (
                "INSERT INTO rail_provider_accounts "
                "(id, provider, credentials_ciphertext) "
                "VALUES ('bad-provider', 'MOCK', 'token')"
            ),
            (
                "INSERT INTO rail_provider_accounts "
                "(id, provider, credentials_ciphertext) "
                "VALUES ('blank-secret', 'SRT', '   ')"
            ),
            (
                "INSERT INTO rail_provider_accounts "
                "(id, provider, credentials_ciphertext, credential_version) "
                "VALUES ('bad-version', 'SRT', 'token', 0)"
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)

    command.downgrade(config, "0016_admin_ui_preferences")
    with sqlite3.connect(database_path) as connection:
        watch_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(watches)")
        }
        assert "reservation_policy" not in watch_columns
        account_table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'rail_provider_accounts'"
        ).fetchone()
        assert account_table is None

    get_settings.cache_clear()
