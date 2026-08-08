from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import rail_waitlist.station_catalog_cache as legacy_station_catalog
from rail_waitlist.timetable_management import catalog_application
from rail_waitlist.timetable_management.contracts import StationCatalogReader

API_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_station_catalog_symbols_are_exact_canonical_objects() -> None:
    assert (
        legacy_station_catalog.StationCatalogSnapshot is catalog_application.StationCatalogSnapshot
    )
    assert (
        legacy_station_catalog.StationCatalogRepository
        is catalog_application.StationCatalogRepository
    )
    assert legacy_station_catalog.StationCatalogService is catalog_application.StationCatalogService
    assert legacy_station_catalog.CANONICAL_CACHE_KEY == catalog_application.CANONICAL_CACHE_KEY
    assert (
        legacy_station_catalog.STATION_CATALOG_SCHEMA_VERSION
        == catalog_application.STATION_CATALOG_SCHEMA_VERSION
    )
    assert legacy_station_catalog.STATION_CATALOG_TTL == catalog_application.STATION_CATALOG_TTL
    assert legacy_station_catalog.REFRESH_LEASE == catalog_application.REFRESH_LEASE
    assert (
        legacy_station_catalog.COLLECTION_TIMEOUT_SECONDS
        == catalog_application.COLLECTION_TIMEOUT_SECONDS
    )
    assert legacy_station_catalog.INITIAL_WAIT_SECONDS == catalog_application.INITIAL_WAIT_SECONDS
    assert (
        legacy_station_catalog.OTHER_OWNER_POLL_SECONDS
        == catalog_application.OTHER_OWNER_POLL_SECONDS
    )


def test_station_catalog_classes_report_the_canonical_owner() -> None:
    assert (
        catalog_application.StationCatalogSnapshot.__module__
        == "rail_waitlist.timetable_management.catalog_application"
    )


def test_canonical_service_satisfies_the_http_reader_contract() -> None:
    assert issubclass(catalog_application.StationCatalogService, StationCatalogReader)
    assert not isinstance(object(), StationCatalogReader)


def test_station_catalog_facade_exports_only_the_compatibility_contract() -> None:
    assert set(legacy_station_catalog.__all__) == {
        "CANONICAL_CACHE_KEY",
        "COLLECTION_TIMEOUT_SECONDS",
        "INITIAL_WAIT_SECONDS",
        "OTHER_OWNER_POLL_SECONDS",
        "REFRESH_LEASE",
        "STATION_CATALOG_SCHEMA_VERSION",
        "STATION_CATALOG_TTL",
        "StationCatalogRepository",
        "StationCatalogService",
        "StationCatalogSnapshot",
    }


def test_station_catalog_import_orders_preserve_exact_class_identity() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.timetable_management import catalog_application as Canonical
    import rail_waitlist.station_catalog_cache as Legacy
else:
    import rail_waitlist.station_catalog_cache as Legacy
    from rail_waitlist.timetable_management import catalog_application as Canonical

print(json.dumps({
    "repository": Legacy.StationCatalogRepository is Canonical.StationCatalogRepository,
    "service": Legacy.StationCatalogService is Canonical.StationCatalogService,
    "snapshot": Legacy.StationCatalogSnapshot is Canonical.StationCatalogSnapshot,
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
            "repository": True,
            "service": True,
            "snapshot": True,
        }
    assert (
        catalog_application.StationCatalogRepository.__module__
        == "rail_waitlist.timetable_management.catalog_application"
    )
    assert (
        catalog_application.StationCatalogService.__module__
        == "rail_waitlist.timetable_management.catalog_application"
    )
