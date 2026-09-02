from __future__ import annotations

import importlib.util
import sqlite3
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
MIGRATION_PATH = (
    API_ROOT / "migrations" / "versions" / "0038_reservation_reconciliation_resolution.py"
)
EXPECTED_CONSTRAINT_NAMES = {
    "ck_reservation_attempt_reconcile_resolution_allowed",
    "ck_reservation_attempt_reconcile_resolution_shape",
}


def _load_migration_0038() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0038_for_test", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0038_compiles_portable_constraints_and_safe_downgrade_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_0038()
    assert migration.revision == "0038_reconciliation_resolution"
    assert migration.down_revision == "0037_standing_only_status"
    assert len(migration.revision) <= 32

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
        and "reconciliation_resolution" in str(constraint.sqltext)
    }
    assert model_constraint_names == EXPECTED_CONSTRAINT_NAMES
    assert all(
        len(name) <= context.dialect.max_identifier_length for name in EXPECTED_CONSTRAINT_NAMES
    )
    for name in EXPECTED_CONSTRAINT_NAMES:
        assert f"ADD CONSTRAINT {name}" in sql
        assert f"DROP CONSTRAINT {name}" in sql

    shape_drop = sql.index("DROP CONSTRAINT ck_reservation_attempt_reconcile_resolution_shape")
    rollback_normalization = sql.index(
        "UPDATE reservation_attempts SET next_reconcile_at = COALESCE"
    )
    allowed_drop = sql.index("DROP CONSTRAINT ck_reservation_attempt_reconcile_resolution_allowed")
    assert shape_drop < rollback_normalization < allowed_drop


def test_migration_0038_backfills_terminal_resolution_and_downgrades_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration-0038.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == "0041_station_cache_v4"

    command.upgrade(config, "0037_standing_only_status")
    observed_at = "2030-08-01T00:01:00+00:00"
    scheduled_at = "2030-08-01T00:02:00+00:00"
    rows = (
        ("legacy-confirmed", 1, "UNKNOWN", "NOT_FOUND", None, None, 0, None),
        ("confirmed", 2, "UNKNOWN", "NOT_FOUND", None, observed_at, 2, None),
        (
            "exhausted-inconclusive",
            3,
            "UNKNOWN",
            "INCONCLUSIVE",
            "UNSPECIFIED",
            None,
            6,
            None,
        ),
        (
            "stranded-inconclusive",
            4,
            "UNKNOWN",
            "INCONCLUSIVE",
            "UNSPECIFIED",
            None,
            6,
            scheduled_at,
        ),
        ("stranded-not-found", 5, "UNKNOWN", "NOT_FOUND", None, None, 6, scheduled_at),
        ("pending-not-found", 6, "UNKNOWN", "NOT_FOUND", None, None, 5, scheduled_at),
        (
            "early-inconclusive",
            7,
            "UNKNOWN",
            "INCONCLUSIVE",
            "UNSPECIFIED",
            observed_at,
            3,
            None,
        ),
        ("payment-not-found", 8, "PAYMENT_REQUIRED", "NOT_FOUND", None, None, 0, None),
        ("unknown-auth", 9, "UNKNOWN", "AUTH_REQUIRED", None, observed_at, 1, None),
        (
            "payment-blocked",
            10,
            "PAYMENT_REQUIRED",
            "PROVIDER_BLOCKED",
            None,
            observed_at,
            4,
            None,
        ),
    )
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO reservation_attempts "
            "(id, candidate_id, attempt_sequence, episode_key, idempotency_key, "
            "started_at, outcome, result_reason_code, confirmation_outcome, "
            "confirmation_diagnostic_code, confirmation_source, confirmation_observed_at, "
            "last_reconciled_at, reconciliation_attempt_count, next_reconcile_at) "
            "VALUES (?, 'candidate-1', ?, ?, ?, '2030-08-01T00:00:00+00:00', ?, "
            "'RESERVATION_REQUEST_RESULT_UNKNOWN', ?, ?, 'safe-migration-source', ?, ?, ?, ?)",
            (
                (
                    attempt_id,
                    sequence,
                    f"episode-{attempt_id}",
                    f"idempotency-{attempt_id}",
                    outcome,
                    confirmation_outcome,
                    diagnostic,
                    observed_at,
                    last_reconciled_at,
                    attempt_count,
                    next_reconcile_at,
                )
                for (
                    attempt_id,
                    sequence,
                    outcome,
                    confirmation_outcome,
                    diagnostic,
                    last_reconciled_at,
                    attempt_count,
                    next_reconcile_at,
                ) in rows
            ),
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(reservation_attempts)")
        }
        assert columns["reconciliation_resolution"][3] == 0
        assert columns["reconciliation_resolution"][4] is None

        actual = {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT id, reconciliation_resolution, reconciliation_attempt_count, "
                "last_reconciled_at, next_reconcile_at FROM reservation_attempts"
            )
        }
        assert actual["legacy-confirmed"][0] is None
        assert actual["legacy-confirmed"][1] == 0
        assert actual["legacy-confirmed"][2] is None
        assert actual["legacy-confirmed"][3] is None
        assert actual["confirmed"][0] == "CONFIRMED_ABSENT"
        assert actual["exhausted-inconclusive"][0] == "EXHAUSTED_UNRESOLVED"
        assert actual["exhausted-inconclusive"][2] is not None
        assert actual["stranded-inconclusive"][0] == "EXHAUSTED_UNRESOLVED"
        assert actual["stranded-inconclusive"][2] is not None
        assert actual["stranded-inconclusive"][3] is None
        assert actual["stranded-not-found"][0] == "EXHAUSTED_UNRESOLVED"
        assert actual["stranded-not-found"][2] is not None
        assert actual["stranded-not-found"][3] is None
        assert actual["pending-not-found"][0] is None
        assert actual["pending-not-found"][3] is not None
        assert actual["early-inconclusive"][0] is None
        assert actual["payment-not-found"][0] is None
        assert actual["unknown-auth"][0] is None
        assert actual["unknown-auth"][1] == 0
        assert actual["payment-blocked"][0] is None
        assert actual["payment-blocked"][1] == 3

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE reservation_attempts SET reconciliation_resolution = "
                "'confirmed_absent' WHERE id = 'early-inconclusive'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE reservation_attempts SET reconciliation_resolution = "
                "'CONFIRMED_ABSENT' WHERE id = 'early-inconclusive'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE reservation_attempts SET reconciliation_resolution = "
                "'EXHAUSTED_UNRESOLVED', next_reconcile_at = NULL "
                "WHERE id = 'pending-not-found'"
            )
        connection.rollback()

    command.downgrade(config, "0037_standing_only_status")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(reservation_attempts)")}
        assert "reconciliation_resolution" not in columns
        rollback_state = dict(
            connection.execute(
                "SELECT id, next_reconcile_at FROM reservation_attempts "
                "WHERE id IN ('confirmed', 'stranded-inconclusive', 'stranded-not-found')"
            ).fetchall()
        )
        assert rollback_state["confirmed"] is None
        assert rollback_state["stranded-inconclusive"] is None
        assert rollback_state["stranded-not-found"] is not None
        auth_counts = dict(
            connection.execute(
                "SELECT id, reconciliation_attempt_count FROM reservation_attempts "
                "WHERE id IN ('unknown-auth', 'payment-blocked')"
            ).fetchall()
        )
        assert auth_counts == {"payment-blocked": 4, "unknown-auth": 1}

    get_settings.cache_clear()
