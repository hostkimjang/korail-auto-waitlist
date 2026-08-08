from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint

from rail_waitlist.database import Base
from rail_waitlist.models import StationCatalogCache as LegacyStationCatalogCache
from rail_waitlist.timetable_management.models import StationCatalogCache

API_ROOT = Path(__file__).resolve().parents[1]


def test_station_catalog_cache_legacy_symbol_is_exact_canonical_object() -> None:
    assert LegacyStationCatalogCache is StationCatalogCache
    assert StationCatalogCache.__module__ == "rail_waitlist.timetable_management.models"


def test_station_catalog_cache_preserves_mapper_columns_and_metadata() -> None:
    table = StationCatalogCache.__table__
    assert Base.metadata.tables["station_catalog_cache"] is table
    assert table.metadata is Base.metadata
    assert sum(mapper.class_ is StationCatalogCache for mapper in Base.registry.mappers) == 1
    assert [
        (
            column.name,
            str(column.type),
            column.nullable,
            column.primary_key,
            column.default is not None,
            column.server_default is not None,
        )
        for column in table.columns
    ] == [
        ("cache_key", "VARCHAR(40)", False, True, False, False),
        ("schema_version", "INTEGER", False, False, True, True),
        ("payload", "JSON", True, False, False, False),
        ("station_count", "INTEGER", False, False, True, True),
        ("retrieved_at", "DATETIME", True, False, False, False),
        ("refresh_after", "DATETIME", True, False, False, False),
        ("refresh_owner", "VARCHAR(64)", True, False, False, False),
        ("lease_until", "DATETIME", True, False, False, False),
        ("last_attempt_at", "DATETIME", True, False, False, False),
        ("last_error_category", "VARCHAR(64)", True, False, False, False),
        ("updated_at", "DATETIME", False, False, True, False),
    ]
    assert tuple(column.name for column in table.primary_key.columns) == ("cache_key",)
    assert table.c.schema_version.default.arg == 2
    assert str(table.c.schema_version.server_default.arg) == "2"
    assert table.c.station_count.default.arg == 0
    assert str(table.c.station_count.server_default.arg) == "0"
    assert table.c.payload.type.none_as_null is True
    assert {column.name for column in table.columns if getattr(column.type, "timezone", False)} == {
        "retrieved_at",
        "refresh_after",
        "lease_until",
        "last_attempt_at",
        "updated_at",
    }


def test_station_catalog_cache_preserves_checks_and_independent_mapper_shape() -> None:
    table = StationCatalogCache.__table__
    assert {
        (constraint.name, str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        (
            "ck_station_catalog_cache_canonical_key",
            "cache_key = 'tago_station_catalog_all'",
        ),
        (
            "ck_station_catalog_cache_schema_version_positive",
            "schema_version >= 1",
        ),
        ("ck_station_catalog_cache_count_nonnegative", "station_count >= 0"),
        (
            "ck_station_catalog_cache_payload_nonempty",
            "payload IS NULL OR station_count > 0",
        ),
        (
            "ck_station_catalog_cache_payload_timestamps",
            "payload IS NULL OR (retrieved_at IS NOT NULL AND refresh_after IS NOT NULL)",
        ),
        (
            "ck_station_catalog_cache_owner_nonempty",
            "refresh_owner IS NULL OR length(trim(refresh_owner)) > 0",
        ),
        (
            "ck_station_catalog_cache_error_nonempty",
            "last_error_category IS NULL OR length(trim(last_error_category)) > 0",
        ),
    }
    assert table.indexes == set()
    assert not any(isinstance(constraint, UniqueConstraint) for constraint in table.constraints)
    assert all(not column.foreign_keys for column in table.columns)
    assert list(StationCatalogCache.__mapper__.relationships.keys()) == []


def test_station_catalog_cache_preserves_aware_updated_at_default_and_onupdate() -> None:
    updated_at = StationCatalogCache.__table__.c.updated_at
    default_value = updated_at.default.arg(None)
    onupdate_value = updated_at.onupdate.arg(None)

    assert default_value.tzinfo is not None
    assert default_value.utcoffset() == timedelta(0)
    assert onupdate_value.tzinfo is not None
    assert onupdate_value.utcoffset() == timedelta(0)


def test_station_catalog_cache_import_orders_register_one_mapper() -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.timetable_management.models import StationCatalogCache as Canonical
    configure_mappers()
    from rail_waitlist.models import StationCatalogCache as Legacy
else:
    from rail_waitlist.models import StationCatalogCache as Legacy
    from rail_waitlist.timetable_management.models import StationCatalogCache as Canonical
configure_mappers()

from rail_waitlist.database import Base

print(json.dumps({
    "identity": Legacy is Canonical,
    "table": Base.metadata.tables["station_catalog_cache"] is Canonical.__table__,
    "mapper_count": sum(mapper.class_ is Canonical for mapper in Base.registry.mappers),
    "columns": [column.name for column in Canonical.__table__.columns],
    "relationships": list(Canonical.__mapper__.relationships.keys()),
}, sort_keys=True))
"""

    for import_order in ("canonical-first", "legacy-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "columns": [
                "cache_key",
                "schema_version",
                "payload",
                "station_count",
                "retrieved_at",
                "refresh_after",
                "refresh_owner",
                "lease_until",
                "last_attempt_at",
                "last_error_category",
                "updated_at",
            ],
            "identity": True,
            "mapper_count": 1,
            "relationships": [],
            "table": True,
        }
