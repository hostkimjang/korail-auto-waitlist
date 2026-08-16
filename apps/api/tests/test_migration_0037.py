from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def _table_sql(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_migration_0037_adds_standing_only_and_downgrades_to_sold_out(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "migration-0037.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == ("0040_legacy_failed_unknown")

    command.upgrade(config, "0036_confirmation_diagnostic")
    with sqlite3.connect(database_path) as connection:
        assert "STANDING_ONLY" not in _table_sql(connection, "seat_observations")
        assert "STANDING_ONLY" not in _table_sql(
            connection,
            "korail_browser_seat_snapshots",
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert "STANDING_ONLY" in _table_sql(connection, "seat_observations")
        assert "STANDING_ONLY" in _table_sql(
            connection,
            "korail_browser_seat_snapshots",
        )
        connection.execute(
            "INSERT INTO seat_observations "
            "(id, candidate_id, status, source, observed_at, fresh_until) VALUES "
            "('observation-standing', 'candidate-standing', 'STANDING_ONLY', "
            "'korail-official-page-browser', '2030-07-29 00:00:00', "
            "'2030-07-29 00:00:01')"
        )
        connection.execute(
            "INSERT INTO korail_browser_snapshot_batches "
            "(id, origin, destination, travel_date, passenger_count, source, "
            "observed_at, fresh_until, created_at) VALUES "
            "('batch-standing', '서울', '대전', '2030-07-30', 1, "
            "'korail-official-browser-companion', '2030-07-29 00:00:00', "
            "'2030-07-29 00:02:00', '2030-07-29 00:00:00')"
        )
        connection.execute(
            "INSERT INTO korail_browser_seat_snapshots "
            "(id, batch_id, train_number, departure_at, seat_class, status, created_at) "
            "VALUES ('snapshot-standing', 'batch-standing', '223', "
            "'2030-07-30 13:08:00', 'STANDARD', 'STANDING_ONLY', "
            "'2030-07-29 00:00:00')"
        )
        connection.commit()

    command.downgrade(config, "0036_confirmation_diagnostic")
    with sqlite3.connect(database_path) as connection:
        observation_status = connection.execute(
            "SELECT status FROM seat_observations WHERE id = 'observation-standing'"
        ).fetchone()
        snapshot_status = connection.execute(
            "SELECT status FROM korail_browser_seat_snapshots WHERE id = 'snapshot-standing'"
        ).fetchone()
        assert observation_status == ("SOLD_OUT",)
        assert snapshot_status == ("SOLD_OUT",)
        assert "STANDING_ONLY" not in _table_sql(connection, "seat_observations")
        assert "STANDING_ONLY" not in _table_sql(
            connection,
            "korail_browser_seat_snapshots",
        )

    get_settings.cache_clear()
