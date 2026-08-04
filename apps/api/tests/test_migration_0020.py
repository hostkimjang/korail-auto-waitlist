from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0020_adds_secret_free_confirmation_provenance(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0020.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    command.upgrade(config, "0019_candidate_operational_state")

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(reservation_attempts)")
        }
        assert {
            "credential_version",
            "confirmation_outcome",
            "confirmation_source",
            "confirmation_observed_at",
            "last_reconciled_at",
        }.issubset(columns)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'reservation_attempts'"
        ).fetchone()[0]
        assert "credential_version IS NULL OR credential_version >= 1" in table_sql
        assert "ck_reservation_attempt_confirmation_provenance_shape" in table_sql

    get_settings.cache_clear()


def test_migration_0020_rejects_partial_confirmation_provenance(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0020-checks.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    command.upgrade(config, "head")

    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO watches (
                id, provider, origin, destination, travel_date, time_from, time_to,
                seat_class, passenger_count, train_numbers, notification_channel_ids,
                mode, reservation_policy, status, dedupe_key, reservation_attempted,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "watch-0020", "SRT", "수서", "부산", "2026-08-03", "09:00:00", "12:00:00",
                "standard", 1, "[]", "[]", "official", "RESERVE_ONCE_BEFORE_PAYMENT",
                "WATCHING", "migration-0020", 1, now, now,
            ),
        )
        connection.execute(
            """
            INSERT INTO watch_candidates (
                id, watch_id, train_number, departure_at, scheduled_departure_at,
                seat_class, priority, state, operational_status, booking_window_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "candidate-0020", "watch-0020", "301", "2026-08-03T09:30:00+00:00",
                "2026-08-03T09:30:00+00:00", "STANDARD", 1, "observed", "UNKNOWN", "UNKNOWN",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reservation_attempts (
                    id, candidate_id, attempt_sequence, episode_key, idempotency_key,
                    started_at, outcome, credential_version, confirmation_outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "attempt-0020", "candidate-0020", 1, "availability:first",
                    "reserve:migration-0020", now, "UNKNOWN", 1, "INCONCLUSIVE",
                ),
            )

    get_settings.cache_clear()
