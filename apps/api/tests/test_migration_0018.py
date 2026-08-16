from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0018_replaces_permanent_candidate_fence_with_episode_sequence(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration-0018.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    command.upgrade(config, "0017_provider_accounts_policy")

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
                "watch-before-0018",
                "MOCK",
                "서울",
                "부산",
                "2026-08-03",
                "09:00:00",
                "12:00:00",
                "standard",
                1,
                "[]",
                "[]",
                "official",
                "RESERVE_ONCE_BEFORE_PAYMENT",
                "WATCHING",
                "before-0018",
                1,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO watch_candidates (
                id, watch_id, train_number, departure_at, seat_class, priority, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "candidate-before-0018",
                "watch-before-0018",
                "MOCK-001",
                "2026-08-03T09:30:00+00:00",
                "standard",
                1,
                "observed",
            ),
        )
        connection.execute(
            """
            INSERT INTO reservation_attempts (
                id, candidate_id, idempotency_key, started_at, finished_at, outcome
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "attempt-before-0018",
                "candidate-before-0018",
                "reserve-before-0018",
                now,
                now,
                "NOT_AVAILABLE",
            ),
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        migrated = connection.execute(
            "SELECT attempt_sequence, episode_key FROM reservation_attempts "
            "WHERE id = 'attempt-before-0018'"
        ).fetchone()
        assert migrated == (1, "legacy:candidate-before-0018")

        connection.execute(
            """
            INSERT INTO reservation_attempts (
                id, candidate_id, attempt_sequence, episode_key, idempotency_key,
                started_at, finished_at, outcome, result_reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "attempt-after-0018",
                "candidate-before-0018",
                2,
                "availability-after:sold-out-observation",
                "reserve-after-0018",
                now,
                now,
                "NOT_AVAILABLE",
                "TARGET_NOT_AVAILABLE",
            ),
        )
        connection.commit()

        for sequence, episode, key in (
            (2, "availability-after:another", "duplicate-sequence"),
            (3, "availability-after:sold-out-observation", "duplicate-episode"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO reservation_attempts (
                        id, candidate_id, attempt_sequence, episode_key, idempotency_key,
                        started_at, finished_at, outcome, result_reason_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        "candidate-before-0018",
                        sequence,
                        episode,
                        key,
                        now,
                        now,
                        "FAILED",
                        "RESERVATION_FAILED",
                    ),
                )
            connection.rollback()

    get_settings.cache_clear()
