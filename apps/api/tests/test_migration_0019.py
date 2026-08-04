from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0019_backfills_scheduled_identity_and_enforces_operational_provenance(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration-0019.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    command.upgrade(config, "0018_reservation_episodes")

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
                "watch-before-0019", "MOCK", "서울", "부산", "2026-08-03", "09:00:00", "12:00:00",
                "standard", 1, "[]", "[]", "official", "NOTIFY_ONLY", "WATCHING",
                "before-0019", 0, now, now,
            ),
        )
        connection.execute(
            """
            INSERT INTO watch_candidates (
                id, watch_id, train_number, departure_at, seat_class, priority, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "candidate-before-0019", "watch-before-0019", "MOCK-001",
                "2026-08-03T09:30:00+00:00", "standard", 1, "observed",
            ),
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT departure_at, scheduled_departure_at, operational_status, "
            "booking_window_status "
            "FROM watch_candidates WHERE id = 'candidate-before-0019'"
        ).fetchone()
        assert row[0] == row[1]
        assert row[2:] == ("UNKNOWN", "UNKNOWN")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE watch_candidates SET operational_source = 'test-source' "
                "WHERE id = 'candidate-before-0019'"
            )

    get_settings.cache_clear()
