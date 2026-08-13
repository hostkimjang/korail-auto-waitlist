from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0031_adds_optional_train_type_and_empty_reserved_seats(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "migration-0031.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == ("0034_progress_terminal_time")

    command.upgrade(config, "0030_attempt_progress")
    now = datetime.now(UTC).replace(microsecond=0)
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
                "watch-before-0031",
                "KORAIL",
                "대전",
                "서울",
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
                "before-0031",
                1,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO timetable_seat_evidence (
                id, evidence_hash, provider, origin_node_id, destination_node_id,
                canonical_train_number, departure_at, passenger_count, seat_class,
                status, provenance_kind, source, observed_at, registration_allowed,
                created_at, registration_valid_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evidence-before-0031",
                "e" * 64,
                "KORAIL",
                "0010",
                "0001",
                "26",
                "2026-08-03T09:30:00+00:00",
                1,
                "STANDARD",
                "SOLD_OUT",
                "official_provider",
                "legacy-provider",
                now.isoformat(),
                1,
                now.isoformat(),
                (now + timedelta(minutes=5)).isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO watch_candidates (
                id, watch_id, train_number, departure_at, scheduled_departure_at,
                seat_class, priority, registration_evidence_id, state,
                operational_status, booking_window_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "candidate-before-0031",
                "watch-before-0031",
                "26",
                "2026-08-03T09:30:00+00:00",
                "2026-08-03T09:30:00+00:00",
                "STANDARD",
                1,
                "evidence-before-0031",
                "observed",
                "UNKNOWN",
                "UNKNOWN",
            ),
        )
        connection.execute(
            """
            INSERT INTO reservation_attempts (
                id, candidate_id, attempt_sequence, episode_key, idempotency_key,
                started_at, finished_at, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "attempt-before-0031",
                "candidate-before-0031",
                1,
                "availability:legacy",
                "reserve-before-0031",
                now.isoformat(),
                now.isoformat(),
                "NOT_AVAILABLE",
            ),
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        evidence_columns = {
            row[1]: (row[2], bool(row[3]), row[4])
            for row in connection.execute("PRAGMA table_info(timetable_seat_evidence)")
        }
        attempt_columns = {
            row[1]: (row[2], bool(row[3]), row[4])
            for row in connection.execute("PRAGMA table_info(reservation_attempts)")
        }
        assert evidence_columns["train_type"] == ("VARCHAR(40)", False, None)
        assert attempt_columns["reserved_seats"] == ("JSON", True, "'[]'")
        assert connection.execute(
            "SELECT train_type FROM timetable_seat_evidence WHERE id = 'evidence-before-0031'"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT reserved_seats FROM reservation_attempts WHERE id = 'attempt-before-0031'"
        ).fetchone() == ("[]",)

    command.downgrade(config, "0030_attempt_progress")
    with sqlite3.connect(database_path) as connection:
        evidence_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(timetable_seat_evidence)")
        }
        attempt_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(reservation_attempts)")
        }
        assert "train_type" not in evidence_columns
        assert "reserved_seats" not in attempt_columns

    get_settings.cache_clear()
