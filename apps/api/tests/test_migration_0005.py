from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def test_migration_0005_upgrades_existing_rows_and_round_trips_sqlite(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "migration-0005.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "0004_watch_candidates")
    now = datetime.now(timezone.utc).isoformat()
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
                "watch-before-0005",
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
                "mock",
                "WATCHING",
                "before-0005",
                0,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO watches (
                id, provider, origin, destination, travel_date, time_from, time_to,
                seat_class, passenger_count, train_numbers, notification_channel_ids,
                mode, status, dedupe_key, reservation_attempted, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-attempted-watch",
                "MOCK",
                "서울",
                "부산",
                "2026-08-01",
                "09:00:00",
                "12:00:00",
                "standard",
                1,
                '["MOCK-002"]',
                "[]",
                "mock",
                "RESERVING",
                "legacy-attempted-before-0005",
                1,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO watch_candidates (
                id, watch_id, train_number, departure_at, seat_class, priority
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-attempted-candidate",
                "legacy-attempted-watch",
                "MOCK-002",
                now,
                "standard",
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO watch_candidates (
                id, watch_id, train_number, departure_at, seat_class, priority
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "candidate-before-0005",
                "watch-before-0005",
                "MOCK-001",
                now,
                "standard",
                1,
            ),
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT unchanged_runs FROM watches WHERE id = 'watch-before-0005'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT state, suppressed_by_candidate_id FROM watch_candidates "
            "WHERE id = 'candidate-before-0005'"
        ).fetchone() == ("active", None)
        assert connection.execute(
            "SELECT state FROM watch_candidates "
            "WHERE id = 'legacy-attempted-candidate'"
        ).fetchone() == ("failed",)
        legacy_attempt = connection.execute(
            "SELECT candidate_id, outcome, payment_deadline, official_handoff_url "
            "FROM reservation_attempts "
            "WHERE candidate_id = 'legacy-attempted-candidate'"
        ).fetchone()
        assert legacy_attempt == (
            "legacy-attempted-candidate",
            "UNKNOWN",
            None,
            None,
        )
        assert connection.execute(
            "SELECT status, next_check_at FROM watches "
            "WHERE id = 'legacy-attempted-watch'"
        ).fetchone() == ("AUTH_REQUIRED", None)
        assert connection.execute(
            "SELECT from_status, to_status, reason FROM watch_transition_history "
            "WHERE watch_id = 'legacy-attempted-watch'"
        ).fetchone() == (
            "RESERVING",
            "AUTH_REQUIRED",
            "legacy_reservation_attempt_requires_manual_check",
        )
        legacy_event = connection.execute(
            "SELECT event_type, payload FROM outbox_events "
            "WHERE aggregate_id = 'legacy-attempted-watch' "
            "AND event_type = 'watch.reservation_attempt_recovery_required'"
        ).fetchone()
        assert legacy_event[0] == "watch.reservation_attempt_recovery_required"
        assert legacy_event[1] == "{}"
        assert {
            "seat_observations",
            "reservation_attempts",
            "watch_transition_history",
            "provider_circuits",
        }.issubset(
            {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE watch_candidates SET state = 'invalid' "
                "WHERE id = 'candidate-before-0005'"
            )

    command.downgrade(config, "0004_watch_candidates")
    with sqlite3.connect(database_path) as connection:
        assert "unchanged_runs" not in table_columns(connection, "watches")
        assert "state" not in table_columns(connection, "watch_candidates")
        assert "suppressed_by_candidate_id" not in table_columns(
            connection, "watch_candidates"
        )
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0004_watch_candidates",)

    get_settings.cache_clear()
