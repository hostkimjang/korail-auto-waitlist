from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from rail_waitlist.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_0007_station_catalog_cache_round_trips(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-0007.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    command.upgrade(config, "0006_admin_password_auth")
    command.upgrade(config, "head")
    now = datetime.now(UTC)
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT cache_key, payload, station_count FROM station_catalog_cache"
        ).fetchone()
        assert row == ("tago_station_catalog_all", None, 0)
        connection.execute(
            """
            UPDATE station_catalog_cache
            SET payload = ?, station_count = 1, retrieved_at = ?, refresh_after = ?
            WHERE cache_key = 'tago_station_catalog_all'
            """,
            (
                json.dumps(
                    {
                        "stations": [
                            {
                                "node_id": "N1",
                                "name": "서울",
                                "city_code": "11",
                                "city_name": "서울특별시",
                            }
                        ],
                        "display_stations": [
                            {
                                "node_id": "N1",
                                "name": "서울",
                                "city_code": "11",
                                "city_name": "서울특별시",
                            }
                        ],
                        "visibility": {
                            "source": "korail_station_guide",
                            "url": "https://www.korail.com/public/st_info/station_data.json",
                            "retrieved_at": now.isoformat(),
                            "etag": None,
                            "last_modified": None,
                        },
                    }
                ),
                now.isoformat(),
                (now + timedelta(hours=24)).isoformat(),
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE station_catalog_cache SET station_count = 0 WHERE payload IS NOT NULL"
            )

    command.downgrade(config, "0006_admin_password_auth")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "station_catalog_cache" not in tables
        assert "admin_accounts" in tables

    get_settings.cache_clear()
