from __future__ import annotations

import importlib.util
import sqlite3
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint

from rail_waitlist.config import get_settings
from rail_waitlist.watch_management.models import ReservationAttempt

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = API_ROOT / "migrations" / "versions" / "0036_confirmation_diagnostic.py"
EXPECTED_CONSTRAINT_NAMES = {
    "ck_reservation_attempt_confirm_diag_allowed",
    "ck_reservation_attempt_confirm_diag_inconclusive",
}


def _load_migration_0036() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0036_for_test", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0036_compiles_portable_postgresql_constraint_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_0036()
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(migration, "op", Operations(context))

    migration.upgrade()
    migration.downgrade()

    sql = output.getvalue()
    model_constraint_names = {
        constraint.name
        for constraint in ReservationAttempt.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and "confirmation_diagnostic_code" in str(constraint.sqltext)
    }
    assert model_constraint_names == EXPECTED_CONSTRAINT_NAMES
    assert all(
        len(name) <= context.dialect.max_identifier_length for name in EXPECTED_CONSTRAINT_NAMES
    )
    for name in EXPECTED_CONSTRAINT_NAMES:
        assert f"ADD CONSTRAINT {name}" in sql
        assert f"DROP CONSTRAINT {name}" in sql


def test_migration_0036_backfills_only_inconclusive_confirmation_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration-0036.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == ("0040_legacy_failed_unknown")

    command.upgrade(config, "0035_reservation_result_reason")
    started_at = datetime(2030, 8, 1, tzinfo=UTC).isoformat()
    observed_at = datetime(2030, 8, 1, 0, 1, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        for attempt_id, confirmation_outcome in (
            ("attempt-inconclusive", "INCONCLUSIVE"),
            ("attempt-not-found", "NOT_FOUND"),
        ):
            connection.execute(
                "INSERT INTO reservation_attempts "
                "(id, candidate_id, attempt_sequence, episode_key, idempotency_key, "
                "started_at, outcome, result_reason_code, confirmation_outcome, "
                "confirmation_source, confirmation_observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    "candidate-1",
                    1 if confirmation_outcome == "INCONCLUSIVE" else 2,
                    f"episode-{attempt_id}",
                    f"idempotency-{attempt_id}",
                    started_at,
                    "UNKNOWN",
                    "RESERVATION_REQUEST_RESULT_UNKNOWN",
                    confirmation_outcome,
                    "safe-migration-source",
                    observed_at,
                ),
            )
        connection.execute(
            "INSERT INTO reservation_attempts "
            "(id, candidate_id, attempt_sequence, episode_key, idempotency_key, "
            "started_at, outcome, result_reason_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "attempt-without-confirmation",
                "candidate-1",
                3,
                "episode-without-confirmation",
                "idempotency-without-confirmation",
                started_at,
                "UNKNOWN",
                "RESERVATION_REQUEST_RESULT_UNKNOWN",
            ),
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(reservation_attempts)")
        }
        assert columns["confirmation_diagnostic_code"][3] == 0
        assert columns["confirmation_diagnostic_code"][4] is None
        actual = dict(
            connection.execute(
                "SELECT id, confirmation_diagnostic_code FROM reservation_attempts"
            ).fetchall()
        )
        assert actual == {
            "attempt-inconclusive": "UNSPECIFIED",
            "attempt-not-found": None,
            "attempt-without-confirmation": None,
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE reservation_attempts SET confirmation_diagnostic_code = "
                "'OFFICIAL_READ_UNAVAILABLE' WHERE id = 'attempt-not-found'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE reservation_attempts SET confirmation_diagnostic_code = "
                "'SOURCE_UNAVAILABLE' WHERE id = 'attempt-inconclusive'"
            )
        connection.execute(
            "UPDATE reservation_attempts SET confirmation_diagnostic_code = NULL "
            "WHERE id = 'attempt-inconclusive'"
        )

    command.downgrade(config, "0035_reservation_result_reason")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(reservation_attempts)")}
        assert "confirmation_diagnostic_code" not in columns

    get_settings.cache_clear()
