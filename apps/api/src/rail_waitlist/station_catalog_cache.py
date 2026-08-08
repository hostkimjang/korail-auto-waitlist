"""Compatibility facade for the timetable station-catalog application."""

from .timetable_management.catalog_application import (
    CANONICAL_CACHE_KEY,
    COLLECTION_TIMEOUT_SECONDS,
    INITIAL_WAIT_SECONDS,
    OTHER_OWNER_POLL_SECONDS,
    REFRESH_LEASE,
    STATION_CATALOG_SCHEMA_VERSION,
    STATION_CATALOG_TTL,
    StationCatalogRepository,
    StationCatalogService,
    StationCatalogSnapshot,
)

__all__ = [
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
]
