from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import srt_seat_source as legacy
from rail_waitlist.provider_adapters import srt_seat_source as owner

API_ROOT = Path(__file__).resolve().parents[1]

PUBLIC_SYMBOLS = {
    "Callable",
    "CooldownStore",
    "KOREA",
    "MemoryCooldownStore",
    "ObservationErrorCategory",
    "Protocol",
    "Provider",
    "RequestException",
    "SOURCE_NAME",
    "SRTError",
    "SRTNetFunnelError",
    "SeatAvailabilityAction",
    "SeatAvailabilityNotObservedReason",
    "SeatAvailabilityProvenance",
    "SeatAvailabilityStatus",
    "SeatClass",
    "SeatClassAvailability",
    "SeatObservationRequest",
    "SeatObservationResult",
    "SrtClientFactory",
    "SrtLiveSeatSource",
    "SrtLiveTimetableUnavailable",
    "SrtOfficialTimetableTrain",
    "SrtSeatSnapshot",
    "SrtStationRosterUnavailable",
    "TimetableItem",
    "UTC",
    "ZoneInfo",
    "asyncio",
    "dataclass",
    "datetime",
    "load_srt_station_roster",
    "map_srt_seat_state",
    "normalize_srt_date",
    "normalize_srt_time",
    "normalize_srt_train_number",
    "time",
    "timedelta",
}
PRIVATE_SYMBOLS = {
    "_AccountlessSrtClient",
    "_CacheEntry",
    "_ProviderCooldown",
    "_SrTrainCodeAwareClient",
    "_SrtClient",
    "_SrtTrain",
    "_default_client_factory",
    "_official_datetime",
    "_optional_date",
    "_optional_nonnegative_int",
    "_optional_text",
    "_optional_time",
    "_snapshot_station_name",
}
OWNER_DEFINITIONS = {
    "SrtLiveTimetableUnavailable",
    "_SrtTrain",
    "_SrtClient",
    "_SrTrainCodeAwareClient",
    "SrtSeatSnapshot",
    "SrtOfficialTimetableTrain",
    "_CacheEntry",
    "_ProviderCooldown",
    "_AccountlessSrtClient",
    "_default_client_factory",
    "map_srt_seat_state",
    "_optional_text",
    "_optional_date",
    "_optional_time",
    "_optional_nonnegative_int",
    "_official_datetime",
    "_snapshot_station_name",
    "SrtLiveSeatSource",
}
LEGACY_PICKLES = {
    "SrtSeatSnapshot": (
        "gASVNQAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3NlYXRfc291cmNllIwPU3J0U2VhdFNuYXBzaG90lJOULg=="
    ),
    "SrtOfficialTimetableTrain": (
        "gASVPwAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3NlYXRfc291cmNllIwZ"
        "U3J0T2ZmaWNpYWxUaW1ldGFibGVUcmFpbpSTlC4="
    ),
    "SrtLiveTimetableUnavailable": (
        "gASVQQAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3NlYXRfc291cmNllIwb"
        "U3J0TGl2ZVRpbWV0YWJsZVVuYXZhaWxhYmxllJOULg=="
    ),
    "_CacheEntry": (
        "gASVMQAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3NlYXRfc291cmNllIwLX0NhY2hlRW50cnmUk5Qu"
    ),
}


def test_legacy_surface_is_the_exact_owner_surface() -> None:
    assert {name for name in vars(legacy) if not name.startswith("_")} == (
        PUBLIC_SYMBOLS | {"annotations"}
    )
    for symbol in PUBLIC_SYMBOLS | PRIVATE_SYMBOLS | {"annotations"}:
        assert getattr(legacy, symbol) is getattr(owner, symbol)


def test_legacy_module_is_an_assignment_only_exact_facade() -> None:
    path = API_ROOT / "src" / "rail_waitlist" / "srt_seat_source.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name != "annotations"
    }
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert definitions == set()
    assert imports == {("provider_adapters", 1, "srt_seat_source", "_source")}
    assert set(assignments) == PUBLIC_SYMBOLS | PRIVATE_SYMBOLS
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_source"
        assert value.attr == symbol


def test_owner_has_the_exact_definition_and_import_boundary() -> None:
    path = API_ROOT / "src" / "rail_waitlist" / "provider_adapters" / "srt_seat_source.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    direct_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert definitions == OWNER_DEFINITIONS
    assert direct_imports == {"asyncio", "logging", "time"}
    assert imports_from == {
        ("__future__", 0),
        ("collections.abc", 0),
        ("dataclasses", 0),
        ("datetime", 0),
        ("typing", 0),
        ("zoneinfo", 0),
        ("pydantic", 0),
        ("requests", 0),
        ("SRT", 0),
        ("SRT.errors", 0),
        ("domain", 2),
        ("observations.contracts", 2),
        ("seat_status_cooldown", 2),
        ("timetable_management.schemas", 2),
        ("srt_identity", 1),
        ("srt_netfunnel_logging", 1),
        ("srt_station_roster", 1),
    }


def test_owner_definitions_report_the_canonical_module() -> None:
    for symbol in OWNER_DEFINITIONS:
        value = getattr(owner, symbol)
        assert value.__module__ == "rail_waitlist.provider_adapters.srt_seat_source"


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_legacy_pickle_globals_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize(
    "first_import",
    ["owner", "legacy", "source-runtime", "sidecar-runtime", "timetable"],
)
def test_import_orders_keep_one_owner_without_canonical_reentry(first_import: str) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "owner": "rail_waitlist.provider_adapters.srt_seat_source",
    "legacy": "rail_waitlist.srt_seat_source",
    "source-runtime": "rail_waitlist.provider_adapters.srt_source_runtime",
    "sidecar-runtime": "rail_waitlist.srt_sidecar.runtime",
    "timetable": "rail_waitlist.timetable_management.application",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before_explicit_import = "rail_waitlist.srt_seat_source" in sys.modules
owner = importlib.import_module("rail_waitlist.provider_adapters.srt_seat_source")
legacy = importlib.import_module("rail_waitlist.srt_seat_source")
symbols = (
    "SrtLiveSeatSource",
    "SrtLiveTimetableUnavailable",
    "SrtSeatSnapshot",
    "SrtOfficialTimetableTrain",
    "_AccountlessSrtClient",
)
print(json.dumps({
    "identity": all(getattr(legacy, symbol) is getattr(owner, symbol) for symbol in symbols),
    "legacy_loaded_before": legacy_loaded_before_explicit_import,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, first_import],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["identity"] is True
    assert result["legacy_loaded_before"] is (first_import == "legacy")


def test_legacy_dependency_reassignment_does_not_mutate_the_owner() -> None:
    original = owner.load_srt_station_roster
    replacement = object()
    try:
        legacy.load_srt_station_roster = replacement  # type: ignore[assignment]
        assert legacy.load_srt_station_roster is replacement
        assert owner.load_srt_station_roster is original
    finally:
        legacy.load_srt_station_roster = original
