from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def _column_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(reservation_attempts)").fetchall()
    }


def test_migration_0039_adds_private_correlation_seats_with_empty_backfill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration-0039.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == ("0040_legacy_failed_unknown")

    command.upgrade(config, "0038_reconciliation_resolution")
    started_at = datetime(2030, 8, 1, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        assert "confirmation_correlation_seats" not in _column_names(connection)
        connection.execute(
            "INSERT INTO reservation_attempts "
            "(id, candidate_id, attempt_sequence, episode_key, idempotency_key, "
            "started_at, outcome, result_reason_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-unknown",
                "candidate-1",
                1,
                "availability:candidate-1:observation-1",
                "reservation:legacy-unknown",
                started_at,
                "UNKNOWN",
                "RESERVATION_REQUEST_RESULT_UNKNOWN",
            ),
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert "confirmation_correlation_seats" in _column_names(connection)
        value = connection.execute(
            "SELECT confirmation_correlation_seats FROM reservation_attempts "
            "WHERE id = 'legacy-unknown'"
        ).fetchone()
        assert value == ("[]",)

    command.downgrade(config, "0038_reconciliation_resolution")
    with sqlite3.connect(database_path) as connection:
        assert "confirmation_correlation_seats" not in _column_names(connection)
        assert connection.execute(
            "SELECT outcome FROM reservation_attempts WHERE id = 'legacy-unknown'"
        ).fetchone() == ("UNKNOWN",)

    get_settings.cache_clear()
