from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_reservation_driver as legacy
from rail_waitlist.korail_sidecar.pydoll import reservation_driver as owner

API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"

PUBLIC_SYMBOLS = {
    "Any",
    "Awaitable",
    "BrowserSourceUnavailable",
    "Callable",
    "CurrentSchedule",
    "KORAIL_ROUTE_HEADING",
    "KorailCredentialInput",
    "KorailReservationOutcome",
    "KorailReservationRequest",
    "KorailReservationResult",
    "Mapping",
    "Protocol",
    "PydollPageSnapshot",
    "PydollReservationDomDriver",
    "ReadControlState",
    "ReservationAttemptState",
    "ReservationControlState",
    "ReservationDomCompatibilityPort",
    "ReservationExecuteScript",
    "VisibleElements",
    "annotations",
    "asyncio",
    "booking_seat_control_key",
    "dataclass",
    "date",
    "datetime",
    "is_rate_limit_response",
    "logging",
    "normalize_korail_station",
    "normalize_korail_train_number",
    "protection_trigger_from_http_response",
    "protection_trigger_from_text",
    "re",
    "urlsplit",
}
PRIVATE_SYMBOLS = {
    "_has_exact_train_number_marker",
    "_normalized_train_number",
    "_reservation_date_markers",
    "_single_reserved_seat",
    "_sanitized_class_tokens",
}
OWNER_DEFINITIONS = {
    "CurrentSchedule",
    "PydollReservationDomDriver",
    "ReadControlState",
    "ReservationAttemptState",
    "ReservationControlState",
    "ReservationDomCompatibilityPort",
    "ReservationExecuteScript",
    "VisibleElements",
    "_has_exact_train_number_marker",
    "_normalized_train_number",
    "_reservation_date_markers",
    "_single_reserved_seat",
    "_sanitized_class_tokens",
}
LEGACY_PICKLES = {
    "ReservationAttemptState": (
        "gASVTgAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjBdSZXNlcnZhdGlvbkF0dGVtcHRTdGF0ZZSTlC4="
    ),
    "ReservationControlState": (
        "gASVTgAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjBdSZXNlcnZhdGlvbkNvbnRyb2xTdGF0ZZSTlC4="
    ),
    "ReservationDomCompatibilityPort": (
        "gASVVgAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjB9SZXNlcnZhdGlvbkRvbUNvbXBhdGliaWxpdHlQb3J0lJOULg=="
    ),
    "ReservationExecuteScript": (
        "gASVTwAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjBhSZXNlcnZhdGlvbkV4ZWN1dGVTY3JpcHSUk5Qu"
    ),
    "VisibleElements": (
        "gASVRgAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjA9WaXNpYmxlRWxlbWVudHOUk5Qu"
    ),
    "CurrentSchedule": (
        "gASVRgAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjA9DdXJyZW50U2NoZWR1bGWUk5Qu"
    ),
    "ReadControlState": (
        "gASVRwAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjBBSZWFkQ29udHJvbFN0YXRllJOULg=="
    ),
    "_normalized_train_number": (
        "gASVTwAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjBhfbm9ybWFsaXplZF90cmFpbl9udW1iZXKUk5Qu"
    ),
    "_has_exact_train_number_marker": (
        "gASVVQAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjB5faGFzX2V4YWN0X3RyYWluX251bWJlcl9tYXJrZXKUk5Qu"
    ),
    "_reservation_date_markers": (
        "gASVUAAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjBlfcmVzZXJ2YXRpb25fZGF0ZV9tYXJrZXJzlJOULg=="
    ),
    "_sanitized_class_tokens": (
        "gASVTgAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjBdfc2FuaXRpemVkX2NsYXNzX3Rva2Vuc5STlC4="
    ),
    "PydollReservationDomDriver": (
        "gASVUQAAAAAAAACMLnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9k"
        "cml2ZXKUjBpQeWRvbGxSZXNlcnZhdGlvbkRvbURyaXZlcpSTlC4="
    ),
}


def test_legacy_reservation_driver_is_a_definition_free_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_pydoll_reservation_driver.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    type_aliases = {node.name.id for node in tree.body if isinstance(node, ast.TypeAlias)}
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
    assert type_aliases == set()
    assert imports == {
        ("korail_sidecar.pydoll", 1, "reservation_driver", "_owner"),
        ("korail_sidecar.pydoll.reservation_driver", 1, "Callable", "Callable"),
    }
    assert set(assignments) == (PUBLIC_SYMBOLS - {"Callable"}) | PRIVATE_SYMBOLS
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    private_names = {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    }
    assert private_names == PRIVATE_SYMBOLS
    assert not hasattr(legacy, "__all__")
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol
        assert getattr(legacy, symbol) is getattr(owner, symbol)
    assert legacy.Callable is owner.Callable


def test_reservation_driver_definitions_have_one_canonical_owner() -> None:
    for symbol in OWNER_DEFINITIONS:
        value = getattr(owner, symbol)
        assert value.__module__ == owner.__name__
        assert getattr(legacy, symbol) is value

    assert browser.PydollReservationDomDriver is owner.PydollReservationDomDriver
    assert browser.ReservationControlState is owner.ReservationControlState
    assert browser._ReservationAttemptState is owner.ReservationAttemptState


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_pre_move_reservation_driver_pickles_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser"])
def test_reservation_driver_import_orders_keep_one_owner_without_legacy_reentry(
    first_import: str,
) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.reservation_driver",
    "legacy": "rail_waitlist.korail_pydoll_reservation_driver",
    "browser": "rail_waitlist.korail_pydoll_browser",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_reservation_driver" in sys.modules
optional_backend_loaded = any(
    name == "pydoll" or name.startswith("pydoll.") for name in sys.modules
)
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_reservation_driver as legacy
from rail_waitlist.korail_sidecar.pydoll import reservation_driver as owner

print(json.dumps({
    "identity": all((
        browser.PydollReservationDomDriver is owner.PydollReservationDomDriver,
        browser.PydollReservationDomDriver is legacy.PydollReservationDomDriver,
        browser.ReservationControlState is owner.ReservationControlState,
        browser._ReservationAttemptState is owner.ReservationAttemptState,
    )),
    "legacy_loaded_before": legacy_loaded_before,
    "optional_backend_loaded": optional_backend_loaded,
    "modules": sorted({
        owner.ReservationAttemptState.__module__,
        owner.ReservationControlState.__module__,
        owner.PydollReservationDomDriver.__module__,
    }),
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, first_import],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "identity": True,
        "legacy_loaded_before": first_import == "legacy",
        "modules": ["rail_waitlist.korail_sidecar.pydoll.reservation_driver"],
        "optional_backend_loaded": False,
    }


def test_reservation_driver_has_one_canonical_consumer_and_no_legacy_reentry() -> None:
    canonical_consumers: set[str] = set()
    legacy_consumers: set[str] = set()
    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "reservation_driver.py"
    owner_tree = ast.parse(
        owner_path.read_text(encoding="utf-8"),
        filename=str(owner_path),
    )

    scan_roots = (SOURCE_ROOT, API_ROOT / "scripts", REPOSITORY_ROOT / "scripts")
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module is None:
                    continue
                if node.module.endswith("korail_sidecar.pydoll.reservation_driver"):
                    if path != SOURCE_ROOT / "korail_pydoll_reservation_driver.py":
                        canonical_consumers.add(relative_path)
                elif node.module.endswith("korail_pydoll_reservation_driver"):
                    legacy_consumers.add(relative_path)

    owner_imports = {
        node.module
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert canonical_consumers == {"apps/api/src/rail_waitlist/korail_pydoll_browser.py"}
    assert legacy_consumers == set()
    assert owner_imports.isdisjoint(
        {
            "korail_pydoll_browser",
            "korail_pydoll_reservation_actor",
            "korail_sidecar.pydoll.reservation_actor",
            "korail_pydoll_reservation_driver",
        }
    )
