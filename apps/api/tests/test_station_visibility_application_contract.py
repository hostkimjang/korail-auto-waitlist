from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import rail_waitlist.station_visibility as legacy_station_visibility
from rail_waitlist.timetable_management import station_visibility

API_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONTRACT = {
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
}


def test_legacy_station_visibility_symbols_are_exact_canonical_objects() -> None:
    for symbol in PUBLIC_CONTRACT:
        assert getattr(legacy_station_visibility, symbol) is getattr(station_visibility, symbol)


def test_station_visibility_facade_exports_only_the_compatibility_contract() -> None:
    assert set(legacy_station_visibility.__all__) == PUBLIC_CONTRACT


def test_station_visibility_implementation_reports_the_canonical_owner() -> None:
    assert (
        station_visibility.StationVisibilityRoster.__module__
        == "rail_waitlist.timetable_management.station_visibility"
    )
    assert (
        station_visibility.KorailStationVisibility.__module__
        == "rail_waitlist.timetable_management.station_visibility"
    )
    assert (
        station_visibility.filter_station_items.__module__
        == "rail_waitlist.timetable_management.station_visibility"
    )


def test_station_visibility_import_orders_preserve_exact_identity() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.timetable_management import station_visibility as Canonical
    import rail_waitlist.station_visibility as Legacy
else:
    import rail_waitlist.station_visibility as Legacy
    from rail_waitlist.timetable_management import station_visibility as Canonical

print(json.dumps({
    "loader": Legacy.KorailStationVisibility is Canonical.KorailStationVisibility,
    "roster": Legacy.StationVisibilityRoster is Canonical.StationVisibilityRoster,
    "error": Legacy.StationVisibilityUnavailable is Canonical.StationVisibilityUnavailable,
    "filter": Legacy.filter_station_items is Canonical.filter_station_items,
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
            "error": True,
            "filter": True,
            "loader": True,
            "roster": True,
        }
