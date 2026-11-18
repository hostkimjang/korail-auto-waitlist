"""Reviewed station-catalog snapshot shipped inside the API package.

새 대기 등록은 역 목록을 먼저 골라야 시작할 수 있다. TAGO 키가 없거나 첫 수집이 실패하면
이 스냅샷을 대신 제공하되 ``station_catalog_cache``에는 저장하지 않는다. 그래야 나중에 키를
설정하고 재시작했을 때 기존 수집 경로가 곧바로 TAGO에서 새 목록을 받는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from importlib import resources

from pydantic import TypeAdapter, ValidationError

from .schemas import StationItem
from .station_visibility import KORAIL_STATION_DATA_URL

BUNDLED_STATION_CATALOG_FORMAT = "railwait.bundled_station_catalog"
BUNDLED_STATION_CATALOG_FORMAT_VERSION = 1
BUNDLED_STATION_CATALOG_PACKAGE = "rail_waitlist.timetable_management"
BUNDLED_STATION_CATALOG_RESOURCE = ("data", "bundled_station_catalog.json")
BUNDLED_STATION_CATALOG_SOURCE = "TAGO"
BUNDLED_VISIBILITY_SOURCE = "korail_station_guide"

_station_list_adapter = TypeAdapter(list[StationItem])


class BundledStationCatalogError(ValueError):
    """The bundled snapshot is unreadable or violates its reviewed contract."""


@dataclass(frozen=True)
class BundledStationCatalog:
    identity_stations: tuple[StationItem, ...]
    display_stations: tuple[StationItem, ...]
    retrieved_at: datetime
    visibility_url: str
    visibility_retrieved_at: datetime


def _aware_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise BundledStationCatalogError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise BundledStationCatalogError(f"{field} must be an ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BundledStationCatalogError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _stations(value: object, field: str) -> tuple[StationItem, ...]:
    try:
        stations = _station_list_adapter.validate_python(value)
    except ValidationError:
        raise BundledStationCatalogError(f"{field} contains an invalid station") from None
    if not stations:
        raise BundledStationCatalogError(f"{field} must not be empty")
    if len({station.node_id for station in stations}) != len(stations):
        raise BundledStationCatalogError(f"{field} contains duplicate node ids")
    return tuple(stations)


def _optional_text(value: object, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise BundledStationCatalogError(f"{field} must be text when present")


def parse_bundled_station_catalog(
    document: object,
    *,
    schema_version: int,
) -> BundledStationCatalog:
    """Validate a bundled snapshot document; the build script shares these rules."""
    if not isinstance(document, dict):
        raise BundledStationCatalogError("snapshot must be a JSON object")
    if (
        document.get("format") != BUNDLED_STATION_CATALOG_FORMAT
        or document.get("format_version") != BUNDLED_STATION_CATALOG_FORMAT_VERSION
    ):
        raise BundledStationCatalogError("snapshot format is not supported")
    # The display filter changes with the cache schema, so an older snapshot must be rebuilt.
    if document.get("station_catalog_schema_version") != schema_version:
        raise BundledStationCatalogError("snapshot was built for another catalog schema")
    if document.get("source") != BUNDLED_STATION_CATALOG_SOURCE:
        raise BundledStationCatalogError("snapshot source must be TAGO")
    retrieved_at = _aware_timestamp(document.get("retrieved_at"), "retrieved_at")

    visibility = document.get("visibility")
    if not isinstance(visibility, dict):
        raise BundledStationCatalogError("visibility provenance is required")
    if (
        visibility.get("source") != BUNDLED_VISIBILITY_SOURCE
        or visibility.get("url") != KORAIL_STATION_DATA_URL
    ):
        raise BundledStationCatalogError("visibility must name the official KORAIL station guide")
    visibility_retrieved_at = _aware_timestamp(
        visibility.get("retrieved_at"), "visibility.retrieved_at"
    )
    _optional_text(visibility.get("etag"), "visibility.etag")
    _optional_text(visibility.get("last_modified"), "visibility.last_modified")

    identity_stations = _stations(document.get("identity_stations"), "identity_stations")
    display_stations = _stations(document.get("display_stations"), "display_stations")
    identity_ids = {station.node_id for station in identity_stations}
    if not {station.node_id for station in display_stations}.issubset(identity_ids):
        raise BundledStationCatalogError("display stations must be identity stations")
    return BundledStationCatalog(
        identity_stations=identity_stations,
        display_stations=display_stations,
        retrieved_at=retrieved_at,
        visibility_url=KORAIL_STATION_DATA_URL,
        visibility_retrieved_at=visibility_retrieved_at,
    )


@cache
def load_bundled_station_catalog(schema_version: int) -> BundledStationCatalog:
    resource = resources.files(BUNDLED_STATION_CATALOG_PACKAGE).joinpath(
        *BUNDLED_STATION_CATALOG_RESOURCE
    )
    try:
        document: object = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BundledStationCatalogError(
            f"bundled station catalog is unreadable: {type(error).__name__}"
        ) from None
    return parse_bundled_station_catalog(document, schema_version=schema_version)
