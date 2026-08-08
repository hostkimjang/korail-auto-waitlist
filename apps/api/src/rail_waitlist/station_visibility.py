"""Compatibility facade for timetable station-visibility policy."""

from .timetable_management.station_visibility import (
    KORAIL_STATION_DATA_URL,
    MAX_KORAIL_ROSTER_COUNT,
    MIN_KORAIL_ROSTER_COUNT,
    NON_INTERCITY_STATION_NAMES,
    REQUEST_TIMEOUT,
    REQUIRED_STATION_NAMES,
    STATION_NAME_ALIASES,
    KorailStationVisibility,
    StationVisibilityRoster,
    StationVisibilityUnavailable,
    filter_station_items,
    normalize_visibility_station_name,
)

__all__ = [
    "KORAIL_STATION_DATA_URL",
    "MAX_KORAIL_ROSTER_COUNT",
    "MIN_KORAIL_ROSTER_COUNT",
    "NON_INTERCITY_STATION_NAMES",
    "REQUEST_TIMEOUT",
    "REQUIRED_STATION_NAMES",
    "STATION_NAME_ALIASES",
    "KorailStationVisibility",
    "StationVisibilityRoster",
    "StationVisibilityUnavailable",
    "filter_station_items",
    "normalize_visibility_station_name",
]
