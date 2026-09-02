from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0035_backfills_closed_reservation_result_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration-0035.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == "0041_station_cache_v4"

    command.upgrade(config, "0034_progress_terminal_time")
    outcomes = (
        "PENDING",
        "PAYMENT_REQUIRED",
        "RESERVED",
        "NOT_AVAILABLE",
        "AUTH_REQUIRED",
        "PROVIDER_BLOCKED",
        "FAILED",
        "UNKNOWN",
    )
    started_at = datetime(2030, 8, 1, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        for sequence, outcome in enumerate(outcomes, start=1):
            connection.execute(
                "INSERT INTO reservation_attempts "
                "(id, candidate_id, attempt_sequence, episode_key, idempotency_key, "
                "started_at, outcome) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"attempt-{sequence}",
                    "candidate-1",
                    sequence,
                    f"episode-{sequence}",
                    f"idempotency-{sequence}",
                    started_at,
                    outcome,
                ),
            )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(reservation_attempts)")
        }
        assert columns["result_reason_code"][3] == 1
        assert columns["result_reason_code"][4] is None
        actual = dict(
            connection.execute(
                "SELECT outcome, result_reason_code FROM reservation_attempts"
            ).fetchall()
        )
        assert actual == {
            "PENDING": "RESERVATION_PENDING",
            "PAYMENT_REQUIRED": "PAYMENT_HOLD_CREATED",
            "RESERVED": "PAYMENT_HOLD_CREATED",
            "NOT_AVAILABLE": "TARGET_NOT_AVAILABLE",
            "AUTH_REQUIRED": "AUTHENTICATION_REQUIRED",
            "PROVIDER_BLOCKED": "PROVIDER_BLOCKED",
            "FAILED": "RESERVATION_FAILED",
            "UNKNOWN": "RESERVATION_REQUEST_RESULT_UNKNOWN",
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE reservation_attempts SET result_reason_code = 'source_unavailable' "
                "WHERE id = 'attempt-8'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO reservation_attempts "
                "(id, candidate_id, attempt_sequence, episode_key, idempotency_key, "
                "started_at, outcome) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "attempt-reason-omitted",
                    "candidate-1",
                    99,
                    "episode-reason-omitted",
                    "idempotency-reason-omitted",
                    started_at,
                    "UNKNOWN",
                ),
            )

    command.downgrade(config, "0034_progress_terminal_time")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(reservation_attempts)")}
        assert "result_reason_code" not in columns

    get_settings.cache_clear()
