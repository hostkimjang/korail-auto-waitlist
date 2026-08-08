from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_seat_source as legacy
from rail_waitlist.provider_adapters import korail_seat_source as owner

API_ROOT = Path(__file__).resolve().parents[1]

PUBLIC_SYMBOLS = {
    "AdultPassenger",
    "Callable",
    "CooldownStore",
    "HTTPAdapter",
    "KOREA",
    "Korail",
    "KorailClientFactory",
    "KorailError",
    "KorailLiveSeatSource",
    "KorailSeatSnapshot",
    "MemoryCooldownStore",
    "NoResultsError",
    "PROTECTION_MARKERS",
    "PassengerFactory",
    "Protocol",
    "RequestException",
    "SOURCE_NAME",
    "SeatAvailabilityAction",
    "SeatAvailabilityNotObservedReason",
    "SeatAvailabilityProvenance",
    "SeatAvailabilityStatus",
    "SeatClassAvailability",
    "TimetableItem",
    "UTC",
    "ZoneInfo",
    "asyncio",
    "dataclass",
    "datetime",
    "map_korail_seat_state",
    "normalize_date",
    "normalize_time",
    "normalize_train_number",
    "time",
}
PRIVATE_SYMBOLS = {
    "_CacheEntry",
    "_DefaultTimeoutAdapter",
    "_KorailClient",
    "_KorailTrain",
    "_ProviderCooldown",
    "_default_client_factory",
}
OWNER_DEFINITIONS = {
    "KorailLiveSeatSource",
    "KorailSeatSnapshot",
    "_CacheEntry",
    "_DefaultTimeoutAdapter",
    "_KorailClient",
    "_KorailTrain",
    "_ProviderCooldown",
    "_default_client_factory",
    "map_korail_seat_state",
    "normalize_date",
    "normalize_time",
    "normalize_train_number",
}
LEGACY_PICKLES = {
    "KorailLiveSeatSource": (
        "gASVPQAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX3NlYXRfc291cmNllIwU"
        "S29yYWlsTGl2ZVNlYXRTb3VyY2WUk5Qu"
    ),
    "KorailSeatSnapshot": (
        "gASVOwAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX3NlYXRfc291cmNllIwS"
        "S29yYWlsU2VhdFNuYXBzaG90lJOULg=="
    ),
    "_DefaultTimeoutAdapter": (
        "gASVPwAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX3NlYXRfc291cmNllIwW"
        "X0RlZmF1bHRUaW1lb3V0QWRhcHRlcpSTlC4="
    ),
    "_CacheEntry": (
        "gASVNAAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX3NlYXRfc291cmNllIwLX0NhY2hlRW50cnmUk5Qu"
    ),
    "_ProviderCooldown": (
        "gASVOgAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX3NlYXRfc291cmNllIwR"
        "X1Byb3ZpZGVyQ29vbGRvd26Uk5Qu"
    ),
}


def test_legacy_surface_is_the_exact_owner_surface() -> None:
    assert {name for name in vars(legacy) if not name.startswith("_")} == (
        PUBLIC_SYMBOLS | {"annotations"}
    )
    for symbol in PUBLIC_SYMBOLS | PRIVATE_SYMBOLS | {"annotations"}:
        assert getattr(legacy, symbol) is getattr(owner, symbol)


def test_legacy_module_is_an_assignment_only_exact_facade() -> None:
    path = API_ROOT / "src" / "rail_waitlist" / "korail_seat_source.py"
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
    assert imports == {("provider_adapters", 1, "korail_seat_source", "_source")}
    assert set(assignments) == PUBLIC_SYMBOLS | PRIVATE_SYMBOLS
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_source"
        assert value.attr == symbol


def test_owner_has_the_exact_definition_import_and_module_boundary() -> None:
    path = API_ROOT / "src" / "rail_waitlist" / "provider_adapters" / "korail_seat_source.py"
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
    assert direct_imports == {"asyncio", "time"}
    assert imports_from == {
        ("__future__", 0),
        ("collections.abc", 0),
        ("dataclasses", 0),
        ("datetime", 0),
        ("typing", 0),
        ("zoneinfo", 0),
        ("korail2", 0),
        ("korail2.korail2", 0),
        ("pydantic", 0),
        ("requests", 0),
        ("requests.adapters", 0),
        ("domain", 2),
        ("seat_status_cooldown", 2),
        ("timetable_management.schemas", 2),
    }
    for symbol in OWNER_DEFINITIONS:
        assert getattr(owner, symbol).__module__ == (
            "rail_waitlist.provider_adapters.korail_seat_source"
        )


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_legacy_pickle_globals_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["owner", "legacy", "main"])
def test_import_orders_keep_one_owner_without_canonical_reentry(first_import: str) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "owner": "rail_waitlist.provider_adapters.korail_seat_source",
    "legacy": "rail_waitlist.korail_seat_source",
    "main": "rail_waitlist.main",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_seat_source" in sys.modules
owner_loaded_before = "rail_waitlist.provider_adapters.korail_seat_source" in sys.modules
owner = importlib.import_module("rail_waitlist.provider_adapters.korail_seat_source")
legacy = importlib.import_module("rail_waitlist.korail_seat_source")
main = importlib.import_module("rail_waitlist.main")
symbols = (
    "KorailLiveSeatSource",
    "KorailSeatSnapshot",
    "_DefaultTimeoutAdapter",
    "map_korail_seat_state",
)
print(json.dumps({
    "identity": all(getattr(legacy, symbol) is getattr(owner, symbol) for symbol in symbols),
    "legacy_loaded_before": legacy_loaded_before,
    "main_has_accountless_binding": hasattr(main, "KorailLiveSeatSource"),
    "owner_loaded_before": owner_loaded_before,
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
    assert result == {
        "identity": True,
        "legacy_loaded_before": first_import == "legacy",
        "main_has_accountless_binding": False,
        "owner_loaded_before": first_import in {"owner", "legacy"},
    }


def test_legacy_dependency_reassignment_does_not_mutate_the_owner() -> None:
    original = owner.Korail
    replacement = object()
    try:
        legacy.Korail = replacement  # type: ignore[assignment]
        assert legacy.Korail is replacement
        assert owner.Korail is original
    finally:
        legacy.Korail = original
