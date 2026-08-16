from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def _insert_watch_and_candidate(
    connection: sqlite3.Connection,
    *,
    provider: str,
    suffix: str,
    now: str,
) -> str:
    watch_id = f"watch-{suffix}"
    candidate_id = f"candidate-{suffix}"
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
            watch_id,
            provider,
            "대전",
            "서울",
            "2030-08-01",
            "09:00:00",
            "12:00:00",
            "standard",
            1,
            "[]",
            "[]",
            "official",
            "RESERVE_ONCE_BEFORE_PAYMENT",
            "WATCHING",
            f"migration-0040-{suffix}",
            1,
            now,
            now,
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
            candidate_id,
            watch_id,
            "26",
            "2030-08-01T09:30:00+00:00",
            "2030-08-01T09:30:00+00:00",
            "STANDARD",
            1,
            "observed",
            "UNKNOWN",
            "UNKNOWN",
        ),
    )
    return candidate_id


def _insert_attempt(
    connection: sqlite3.Connection,
    *,
    attempt_id: str,
    candidate_id: str,
    sequence: int,
    outcome: str,
    reason: str,
    now: str,
    credential_version: int | None = 1,
    confirmation: bool = False,
) -> None:
    connection.execute(
        """
        INSERT INTO reservation_attempts (
            id, candidate_id, attempt_sequence, episode_key, idempotency_key,
            started_at, finished_at, outcome, result_reason_code, credential_version,
            confirmation_outcome, confirmation_source, confirmation_observed_at,
            reconciliation_attempt_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            attempt_id,
            candidate_id,
            sequence,
            f"availability:{attempt_id}",
            f"reserve:{attempt_id}",
            now,
            now,
            outcome,
            reason,
            credential_version,
            "INCONCLUSIVE" if confirmation else None,
            "official-list" if confirmation else None,
            now if confirmation else None,
        ),
    )


def test_migration_0040_fences_only_legacy_external_provider_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration-0040.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == ("0040_legacy_failed_unknown")

    command.upgrade(config, "0039_confirmation_corr_seats")
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    with sqlite3.connect(database_path) as connection:
        korail = _insert_watch_and_candidate(
            connection, provider="KORAIL", suffix="korail", now=now
        )
        srt = _insert_watch_and_candidate(connection, provider="SRT", suffix="srt", now=now)
        mock = _insert_watch_and_candidate(connection, provider="MOCK", suffix="mock", now=now)
        _insert_attempt(
            connection,
            attempt_id="korail-legacy",
            candidate_id=korail,
            sequence=1,
            outcome="FAILED",
            reason="PROVIDER_UNAVAILABLE",
            now=now,
        )
        _insert_attempt(
            connection,
            attempt_id="srt-legacy",
            candidate_id=srt,
            sequence=1,
            outcome="FAILED",
            reason="PROVIDER_UNAVAILABLE",
            now=now,
        )
        _insert_attempt(
            connection,
            attempt_id="mock-failure",
            candidate_id=mock,
            sequence=1,
            outcome="FAILED",
            reason="PROVIDER_UNAVAILABLE",
            now=now,
        )
        _insert_attempt(
            connection,
            attempt_id="korail-conclusive",
            candidate_id=korail,
            sequence=2,
            outcome="FAILED",
            reason="RESERVATION_FAILED",
            now=now,
        )
        _insert_attempt(
            connection,
            attempt_id="korail-confirmed",
            candidate_id=korail,
            sequence=3,
            outcome="FAILED",
            reason="PROVIDER_UNAVAILABLE",
            now=now,
            confirmation=True,
        )
        _insert_attempt(
            connection,
            attempt_id="korail-existing-unknown",
            candidate_id=korail,
            sequence=4,
            outcome="UNKNOWN",
            reason="PROVIDER_UNAVAILABLE",
            now=now,
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        actual = {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT id, outcome, next_reconcile_at FROM reservation_attempts"
            )
        }
        for attempt_id in ("korail-legacy", "srt-legacy"):
            assert actual[attempt_id][0] == "UNKNOWN"
            assert actual[attempt_id][1] is not None
        for attempt_id in (
            "mock-failure",
            "korail-conclusive",
            "korail-confirmed",
        ):
            assert actual[attempt_id] == ("FAILED", None)
        assert actual["korail-existing-unknown"] == ("UNKNOWN", None)

    command.downgrade(config, "0039_confirmation_corr_seats")
    with sqlite3.connect(database_path) as connection:
        assert dict(
            connection.execute(
                "SELECT id, outcome FROM reservation_attempts "
                "WHERE id IN ('korail-legacy', 'srt-legacy')"
            ).fetchall()
        ) == {"korail-legacy": "UNKNOWN", "srt-legacy": "UNKNOWN"}

    get_settings.cache_clear()
