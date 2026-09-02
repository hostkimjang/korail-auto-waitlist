from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0034_normalizes_terminal_time_after_progress(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration-0034.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == "0041_station_cache_v4"

    command.upgrade(config, "0033_observation_in_flight")
    started_at = datetime(2030, 8, 1, 0, 15, tzinfo=UTC)
    finished_at = started_at + timedelta(seconds=2)
    latest_progress_at = finished_at + timedelta(milliseconds=673)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO reservation_attempts "
            "(id, candidate_id, attempt_sequence, episode_key, idempotency_key, started_at, "
            "finished_at, outcome, progress_stages) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "attempt-clock-rollback",
                "candidate-clock-rollback",
                1,
                "clock-rollback-episode",
                "clock-rollback-attempt",
                started_at.isoformat(),
                finished_at.isoformat(),
                "FAILED",
                json.dumps(
                    [
                        {
                            "stage": "authenticated_session_ready",
                            "occurred_at": latest_progress_at.isoformat(),
                        }
                    ]
                ),
            ),
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        normalized = connection.execute(
            "SELECT finished_at FROM reservation_attempts WHERE id = ?",
            ("attempt-clock-rollback",),
        ).fetchone()[0]
        assert datetime.fromisoformat(normalized) == latest_progress_at

    command.downgrade(config, "0033_observation_in_flight")
    with sqlite3.connect(database_path) as connection:
        preserved = connection.execute(
            "SELECT finished_at FROM reservation_attempts WHERE id = ?",
            ("attempt-clock-rollback",),
        ).fetchone()[0]
        assert datetime.fromisoformat(preserved) == latest_progress_at

    get_settings.cache_clear()
