from __future__ import annotations
import __future__

import ast
import asyncio
import base64
import collections.abc
import dataclasses
import datetime as datetime_module
import ipaddress
import json
import logging
import pickle
import re
import subprocess
import sys
import time as time_module
import typing
import urllib.parse
import zoneinfo
from pathlib import Path
from types import ModuleType

import pydantic

from rail_waitlist import korail_browser_automation as legacy
from rail_waitlist.korail_sidecar import (
    browser_contracts,
    browser_page_contracts,
    browser_protection,
    direct_cdp,
    search_coordinator,
    search_result_policy,
)
from rail_waitlist.korail_sidecar.playwright import client as playwright_client
from rail_waitlist.korail_sidecar.playwright import result_reader as playwright_result_reader
from rail_waitlist.korail_sidecar.playwright import search_form as playwright_search_form
from rail_waitlist.provider_registry import korail_search_url_policy

API_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = API_ROOT / "src" / "rail_waitlist"
SCRIPT_ROOT = API_ROOT / "scripts"
LEGACY_MODULE = "rail_waitlist.korail_browser_automation"
COORDINATOR_MODULE = "rail_waitlist.korail_sidecar.search_coordinator"
PLAYWRIGHT_CLIENT_MODULE = "rail_waitlist.korail_sidecar.playwright.client"
PLAYWRIGHT_RESULT_READER_MODULE = "rail_waitlist.korail_sidecar.playwright.result_reader"
PLAYWRIGHT_SEARCH_FORM_MODULE = "rail_waitlist.korail_sidecar.playwright.search_form"
PAGE_CONTRACT_MODULE = "rail_waitlist.korail_sidecar.browser_page_contracts"

COORDINATOR_SYMBOLS = {"_CacheEntry", "_Cooldown", "KorailBrowserAutomation"}
COORDINATOR_DEFINITIONS = COORDINATOR_SYMBOLS | {"_InflightSearch"}
PLAYWRIGHT_CLIENT_SYMBOLS = {
    "PlaywrightKorailBrowserClient",
    "probe_chromium",
}
PLAYWRIGHT_CLIENT_LEGACY_SYMBOLS = PLAYWRIGHT_CLIENT_SYMBOLS | {"PROTECTION_SURFACE_SELECTOR"}
PLAYWRIGHT_RESULT_READER_LEGACY_SYMBOLS = {
    "ROUTE_HEADING",
    "_normalize_station",
    "_normalize_train_number",
}
PLAYWRIGHT_RESULT_READER_FUNCTIONS = {
    "_normalize_station",
    "_normalize_train_number",
    "read_result",
    "read_seat_status",
    "seat_boxes",
}
PLAYWRIGHT_RESULT_READER_SYMBOLS = (
    PLAYWRIGHT_RESULT_READER_LEGACY_SYMBOLS | PLAYWRIGHT_RESULT_READER_FUNCTIONS
)
PLAYWRIGHT_RESULT_READER_DEFINITIONS = PLAYWRIGHT_RESULT_READER_FUNCTIONS | {"_ResultReaderHost"}
PLAYWRIGHT_SEARCH_FORM_FUNCTIONS = {
    "active_time_hours",
    "assert_pre_submit_identity",
    "choose_departure",
    "choose_station",
    "click_visible_control",
    "departure_input",
    "find_date_link",
    "find_time_control",
    "move_calendar_to_month",
    "move_time_to_hour",
    "passenger_value",
    "release_and_detach_mouse",
    "station_trigger",
    "station_value",
    "submit_search",
    "wait_for_unique_departure_dialog",
    "wait_for_unique_station_result",
}
PLAYWRIGHT_SEARCH_FORM_DEFINITIONS = PLAYWRIGHT_SEARCH_FORM_FUNCTIONS | {"_SearchFormHost"}
PLAYWRIGHT_SEARCH_FORM_WRAPPERS = {f"_{name}" for name in PLAYWRIGHT_SEARCH_FORM_FUNCTIONS}
PAGE_CONTRACT_SYMBOLS = {"FULLSTACK_E2E_PAGE_URL", "OFFICIAL_KORAIL_SEARCH_URL"}

LEGACY_PICKLES = {
    "probe_chromium": (
        "gASVPgAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "DnByb2JlX2Nocm9taXVtlJOULg=="
    ),
    "_CacheEntry": (
        "gASVOwAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "C19DYWNoZUVudHJ5lJOULg=="
    ),
    "_Cooldown": (
        "gASVOQAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "CV9Db29sZG93bpSTlC4="
    ),
    "KorailBrowserAutomation": (
        "gASVRwAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "F0tvcmFpbEJyb3dzZXJBdXRvbWF0aW9ulJOULg=="
    ),
    "PlaywrightKorailBrowserClient": (
        "gASVTQAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "HVBsYXl3cmlnaHRLb3JhaWxCcm93c2VyQ2xpZW50lJOULg=="
    ),
    "_normalize_station": (
        "gASVQgAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "El9ub3JtYWxpemVfc3RhdGlvbpSTlC4="
    ),
    "is_supported_korail_train_kind": (
        "gASVTgAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "HmlzX3N1cHBvcnRlZF9rb3JhaWxfdHJhaW5fa2luZJSTlC4="
    ),
    "_normalize_train_number": (
        "gASVRwAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "F19ub3JtYWxpemVfdHJhaW5fbnVtYmVylJOULg=="
    ),
    "visible_departure_matches": (
        "gASVSQAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "GXZpc2libGVfZGVwYXJ0dXJlX21hdGNoZXOUk5Qu"
    ),
    "KorailBrowserAutomation.search": (
        "gASVTgAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "HktvcmFpbEJyb3dzZXJBdXRvbWF0aW9uLnNlYXJjaJSTlC4="
    ),
    "KorailBrowserAutomation._load": (
        "gASVTQAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "HUtvcmFpbEJyb3dzZXJBdXRvbWF0aW9uLl9sb2FklJOULg=="
    ),
    "KorailBrowserAutomation.drain_pending_calls": (
        "gASVWwAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYXV0b21hdGlvbpSM"
        "K0tvcmFpbEJyb3dzZXJBdXRvbWF0aW9uLmRyYWluX3BlbmRpbmdfY2FsbHOUk5Qu"
    ),
}


def _owner_exports(owner: ModuleType, symbols: set[str]) -> dict[str, object]:
    return {symbol: getattr(owner, symbol) for symbol in symbols}


EXPECTED_EXPORTS = {
    "annotations": __future__.annotations,
    "asyncio": asyncio,
    "ipaddress": ipaddress,
    "logging": logging,
    "re": re,
    "time": time_module,
    "Callable": collections.abc.Callable,
    "dataclass": dataclasses.dataclass,
    "UTC": datetime_module.UTC,
    "date": datetime_module.date,
    "datetime": datetime_module.datetime,
    "clock_time": datetime_module.time,
    "timedelta": datetime_module.timedelta,
    "Literal": typing.Literal,
    "Protocol": typing.Protocol,
    "urlsplit": urllib.parse.urlsplit,
    "ZoneInfo": zoneinfo.ZoneInfo,
    "BaseModel": pydantic.BaseModel,
    "ConfigDict": pydantic.ConfigDict,
    "Field": pydantic.Field,
    "field_validator": pydantic.field_validator,
    "model_validator": pydantic.model_validator,
    **_owner_exports(
        browser_contracts,
        {
            "AdapterErrorReason",
            "AdapterModel",
            "BrowserAdapterError",
            "BrowserClient",
            "BrowserProtectionDetected",
            "BrowserRateLimited",
            "BrowserSeatSearchRequest",
            "BrowserSeatSearchResult",
            "BrowserSourceUnavailable",
            "BrowserTrainSnapshot",
            "KorailTrainType",
            "ProtectionTrigger",
            "SOURCE_NAME",
            "SeatStatus",
        },
    ),
    **_owner_exports(browser_page_contracts, PAGE_CONTRACT_SYMBOLS),
    **_owner_exports(
        browser_protection,
        {
            "GENERIC_PROTECTION_TRIGGERS",
            "PROTECTION_MARKERS",
            "RATE_LIMIT_RESOURCE_TYPES",
            "is_rate_limit_response",
            "protection_trigger_from_http_response",
            "protection_trigger_from_text",
        },
    ),
    **_owner_exports(direct_cdp, {"DirectCdpLaunchError", "open_direct_cdp_browser"}),
    **_owner_exports(search_coordinator, COORDINATOR_SYMBOLS),
    **_owner_exports(
        search_result_policy,
        {
            "ADULT_FARE_PATTERN",
            "DELAY_ESTIMATE_PATTERN",
            "KST",
            "OFFICIAL_TRAIN_TYPE_PATTERN",
            "is_supported_korail_train_kind",
            "parse_expected_delay_minutes",
            "parse_official_train_type",
            "parse_unambiguous_adult_fare",
            "service_datetimes",
            "status_from_seat_box",
            "visible_departure_matches",
        },
    ),
    **_owner_exports(
        playwright_client,
        PLAYWRIGHT_CLIENT_LEGACY_SYMBOLS | {"logger"},
    ),
    **_owner_exports(
        playwright_result_reader,
        PLAYWRIGHT_RESULT_READER_LEGACY_SYMBOLS,
    ),
    "validate_korail_general_search_url": (
        korail_search_url_policy.validate_korail_general_search_url
    ),
}
LEGACY_SYMBOLS = set(EXPECTED_EXPORTS)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _top_level_definitions(path: Path) -> set[str]:
    return {
        node.name
        for node in _tree(path).body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _resolve_import(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = ["rail_waitlist", *path.relative_to(PACKAGE_ROOT).parent.parts]
    keep = len(package) - node.level + 1
    return ".".join([*package[:keep], *(node.module or "").split(".")])


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _legacy_references(source: str, relative_path: Path) -> list[str]:
    tree = ast.parse(source, filename=str(relative_path))
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    violations: list[str] = []
    package_aliases: set[str] = set()
    legacy_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "rail_waitlist":
                    package_aliases.add(alias.asname or "rail_waitlist")
                elif alias.name == LEGACY_MODULE:
                    if alias.asname is None:
                        package_aliases.add("rail_waitlist")
                    else:
                        legacy_aliases.add(alias.asname)
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module")
                elif alias.name == "importlib":
                    importlib_aliases.add(alias.asname or "importlib")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = node.module or ""
        else:
            keep = len(package_parts) - node.level + 1
            resolved = ".".join([*package_parts[:keep], *(node.module or "").split(".")])
        imported_names = {alias.name for alias in node.names}
        if resolved == LEGACY_MODULE and (imported_names & LEGACY_SYMBOLS or "*" in imported_names):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == "rail_waitlist":
            for alias in node.names:
                if alias.name == "korail_browser_automation":
                    legacy_aliases.add(alias.asname or alias.name)
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module-style")
        if resolved == "importlib":
            import_module_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )

    def is_legacy_reference(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in legacy_aliases
        parts = _attribute_chain(node)
        return (
            len(parts) == 2
            and parts[0] in package_aliases
            and parts[1] == "korail_browser_automation"
        )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if value is None:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(value, ast.Name) and value.id in package_aliases:
                    before = len(package_aliases)
                    package_aliases.add(target.id)
                    changed = changed or len(package_aliases) != before
                if is_legacy_reference(value):
                    before = len(legacy_aliases)
                    legacy_aliases.add(target.id)
                    changed = changed or len(legacy_aliases) != before

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = _attribute_chain(node)
            if len(parts) >= 2 and parts[0] in legacy_aliases and parts[-1] in LEGACY_SYMBOLS:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module-attribute")
            if (
                len(parts) >= 3
                and parts[0] in package_aliases
                and parts[1] == "korail_browser_automation"
                and parts[-1] in LEGACY_SYMBOLS
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-attribute")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ):
            if is_legacy_reference(node.args[0]) and node.args[1].value in LEGACY_SYMBOLS:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> getattr-symbol")
            if (
                isinstance(node.args[0], ast.Name)
                and node.args[0].id in package_aliases
                and node.args[1].value == "korail_browser_automation"
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> getattr-module")
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == LEGACY_MODULE
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> __import__")
        dynamic_import = (
            isinstance(node.func, ast.Name) and node.func.id in import_module_aliases
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in importlib_aliases
            and node.func.attr == "import_module"
        )
        if (
            dynamic_import
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == LEGACY_MODULE
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> importlib")

    return violations


def test_legacy_facade_has_no_definitions_and_preserves_the_exact_68_object_surface() -> None:
    facade_path = PACKAGE_ROOT / "korail_browser_automation.py"
    assert _top_level_definitions(facade_path) == set()
    assert "__all__" not in vars(legacy)

    actual = {name: value for name, value in vars(legacy).items() if not name.startswith("__")}
    public_names = {name for name in actual if not name.startswith("_")}
    private_names = set(actual) - public_names

    assert len(public_names) == 64
    assert private_names == {
        "_CacheEntry",
        "_Cooldown",
        "_normalize_station",
        "_normalize_train_number",
    }
    assert set(actual) == LEGACY_SYMBOLS
    for symbol, expected in EXPECTED_EXPORTS.items():
        assert actual[symbol] is expected

    wildcard: dict[str, object] = {}
    exec("from rail_waitlist.korail_browser_automation import *", wildcard)  # noqa: S102
    assert {name for name in wildcard if not name.startswith("_")} == public_names


def test_moved_symbols_have_exact_canonical_owners() -> None:
    assert _top_level_definitions(PACKAGE_ROOT / "korail_sidecar" / "search_coordinator.py") == (
        COORDINATOR_DEFINITIONS
    )
    assert (
        _top_level_definitions(PACKAGE_ROOT / "korail_sidecar" / "playwright" / "client.py")
        == PLAYWRIGHT_CLIENT_SYMBOLS
    )
    assert (
        _top_level_definitions(PACKAGE_ROOT / "korail_sidecar" / "playwright" / "result_reader.py")
        == PLAYWRIGHT_RESULT_READER_DEFINITIONS
    )
    assert (
        _top_level_definitions(PACKAGE_ROOT / "korail_sidecar" / "playwright" / "search_form.py")
        == PLAYWRIGHT_SEARCH_FORM_DEFINITIONS
    )
    assert (
        _top_level_definitions(PACKAGE_ROOT / "korail_sidecar" / "browser_page_contracts.py")
        == set()
    )

    for symbol in COORDINATOR_SYMBOLS:
        assert getattr(search_coordinator, symbol).__module__ == COORDINATOR_MODULE
    for symbol in PLAYWRIGHT_CLIENT_SYMBOLS:
        assert getattr(playwright_client, symbol).__module__ == PLAYWRIGHT_CLIENT_MODULE
    for symbol in PLAYWRIGHT_RESULT_READER_FUNCTIONS:
        assert (
            getattr(playwright_result_reader, symbol).__module__ == PLAYWRIGHT_RESULT_READER_MODULE
        )
    for symbol in PLAYWRIGHT_SEARCH_FORM_FUNCTIONS:
        assert getattr(playwright_search_form, symbol).__module__ == PLAYWRIGHT_SEARCH_FORM_MODULE
    for symbol in PLAYWRIGHT_RESULT_READER_LEGACY_SYMBOLS:
        assert getattr(playwright_client, symbol) is getattr(playwright_result_reader, symbol)
        assert getattr(legacy, symbol) is getattr(playwright_result_reader, symbol)

    client_tree = _tree(PACKAGE_ROOT / "korail_sidecar" / "playwright" / "client.py")
    client_class = next(
        node
        for node in client_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PlaywrightKorailBrowserClient"
    )
    client_methods = {
        node.name
        for node in client_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "_read_result",
        "_read_seat_status",
        "_seat_boxes",
    } | PLAYWRIGHT_SEARCH_FORM_WRAPPERS <= client_methods

    reader_tree = _tree(PACKAGE_ROOT / "korail_sidecar" / "playwright" / "result_reader.py")
    reader_host = next(
        node
        for node in reader_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_ResultReaderHost"
    )
    reader_host_methods = {
        node.name
        for node in reader_host.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert reader_host_methods == {
        "_departure_input",
        "_passenger_value",
        "_read_seat_status",
        "_seat_boxes",
    }
    assert reader_host_methods <= client_methods
    for symbol in PAGE_CONTRACT_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(browser_page_contracts, symbol)


def test_pre_move_pickle_globals_restore_to_the_new_owners() -> None:
    expected = {
        "probe_chromium": playwright_client.probe_chromium,
        "_CacheEntry": search_coordinator._CacheEntry,
        "_Cooldown": search_coordinator._Cooldown,
        "KorailBrowserAutomation": search_coordinator.KorailBrowserAutomation,
        "PlaywrightKorailBrowserClient": playwright_client.PlaywrightKorailBrowserClient,
        "_normalize_station": playwright_result_reader._normalize_station,
        "is_supported_korail_train_kind": (search_result_policy.is_supported_korail_train_kind),
        "_normalize_train_number": playwright_result_reader._normalize_train_number,
        "visible_departure_matches": search_result_policy.visible_departure_matches,
        "KorailBrowserAutomation.search": search_coordinator.KorailBrowserAutomation.search,
        "KorailBrowserAutomation._load": search_coordinator.KorailBrowserAutomation._load,
        "KorailBrowserAutomation.drain_pending_calls": (
            search_coordinator.KorailBrowserAutomation.drain_pending_calls
        ),
    }

    assert set(LEGACY_PICKLES) == set(expected)
    for symbol, payload in LEGACY_PICKLES.items():
        assert pickle.loads(base64.b64decode(payload)) is expected[symbol]


def test_import_orders_keep_one_owner_and_passive_optional_backends() -> None:
    script = r"""
import importlib
import json
import logging
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.playwright.client",
    "reader": "rail_waitlist.korail_sidecar.playwright.result_reader",
    "search_form": "rail_waitlist.korail_sidecar.playwright.search_form",
    "legacy": "rail_waitlist.korail_browser_automation",
    "runtime": "rail_waitlist.korail_sidecar.runtime",
    "adapter": "rail_waitlist.korail_browser_adapter_service",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_browser_automation" in sys.modules

from rail_waitlist import korail_browser_adapter_service as adapter
from rail_waitlist import korail_browser_automation as legacy
from rail_waitlist.korail_sidecar import browser_page_contracts as pages
from rail_waitlist.korail_sidecar import runtime
from rail_waitlist.korail_sidecar import search_coordinator as coordinator
from rail_waitlist.korail_sidecar.playwright import client
from rail_waitlist.korail_sidecar.playwright import result_reader
from rail_waitlist.korail_sidecar.playwright import search_form

optional_modules = sorted(
    name
    for name in sys.modules
    if name == "playwright"
    or name.startswith("playwright.")
    or name == "pydoll"
    or name.startswith("pydoll.")
)
print(json.dumps({
    "automation": (
        legacy.KorailBrowserAutomation
        is coordinator.KorailBrowserAutomation
        is runtime.KorailBrowserAutomation
        is adapter.KorailBrowserAutomation
    ),
    "client": (
        legacy.PlaywrightKorailBrowserClient
        is client.PlaywrightKorailBrowserClient
        is runtime.PlaywrightKorailBrowserClient
    ),
    "reader": all(
        getattr(legacy, name)
        is getattr(client, name)
        is getattr(result_reader, name)
        for name in (
            "ROUTE_HEADING",
            "_normalize_station",
            "_normalize_train_number",
        )
    ),
    "protection_surface": (
        legacy.PROTECTION_SURFACE_SELECTOR is client.PROTECTION_SURFACE_SELECTOR
    ),
    "reader_modules": sorted({
        getattr(result_reader, name).__module__
        for name in (
            "_normalize_station",
            "_normalize_train_number",
            "read_result",
            "read_seat_status",
            "seat_boxes",
        )
    }),
    "legacy_loaded_before": legacy_loaded_before,
    "logger_handlers": len(legacy.logger.handlers),
    "logger_identity": (
        legacy.logger is client.logger is coordinator.logger is search_form.logger
    ),
    "logger_level": legacy.logger.level,
    "logger_name": legacy.logger.name,
    "optional_modules": optional_modules,
    "probe": legacy.probe_chromium is client.probe_chromium is runtime.probe_chromium,
    "urls": all(
        getattr(legacy, name)
        is getattr(client, name)
        is getattr(runtime, name)
        is getattr(pages, name)
        for name in ("FULLSTACK_E2E_PAGE_URL", "OFFICIAL_KORAIL_SEARCH_URL")
    ),
}, sort_keys=True))
"""

    for first_import in (
        "canonical",
        "reader",
        "search_form",
        "legacy",
        "runtime",
        "adapter",
    ):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, first_import],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "automation": True,
            "client": True,
            "legacy_loaded_before": first_import == "legacy",
            "logger_handlers": 0,
            "logger_identity": True,
            "logger_level": logging.NOTSET,
            "logger_name": LEGACY_MODULE,
            "optional_modules": [],
            "probe": True,
            "protection_surface": True,
            "reader": True,
            "reader_modules": [PLAYWRIGHT_RESULT_READER_MODULE],
            "urls": True,
        }


def test_legacy_reference_detector_covers_direct_module_alias_and_dynamic_forms() -> None:
    examples = (
        "from .korail_browser_automation import KorailBrowserAutomation",
        "from rail_waitlist.korail_browser_automation import *",
        "import rail_waitlist.korail_browser_automation as old; old.probe_chromium",
        "from rail_waitlist import korail_browser_automation as old; old._Cooldown",
        "import rail_waitlist as rw; rw.korail_browser_automation.OFFICIAL_KORAIL_SEARCH_URL",
        "import rail_waitlist.korail_browser_automation as old; alias = old; alias._CacheEntry",
        "import rail_waitlist.korail_browser_automation as old; getattr(old, 'probe_chromium')",
        "import rail_waitlist as rw; getattr(rw, 'korail_browser_automation')",
        f"import importlib; importlib.import_module('{LEGACY_MODULE}')",
        f"from importlib import import_module as load; load('{LEGACY_MODULE}')",
        f"__import__('{LEGACY_MODULE}')",
    )

    for source in examples:
        assert _legacy_references(source, Path("rail_waitlist/probe.py")), source


def test_production_has_exact_canonical_consumers_and_no_legacy_reentry() -> None:
    expected_consumers = {
        COORDINATOR_MODULE: {
            "korail_browser_adapter_service.py": {"KorailBrowserAutomation"},
            "korail_browser_automation.py": COORDINATOR_SYMBOLS,
            "korail_sidecar/http.py": {"KorailBrowserAutomation"},
            "korail_sidecar/runtime.py": {"KorailBrowserAutomation"},
        },
        PLAYWRIGHT_CLIENT_MODULE: {
            "korail_browser_adapter_service.py": {"probe_chromium"},
            "korail_browser_automation.py": PLAYWRIGHT_CLIENT_LEGACY_SYMBOLS,
            "korail_sidecar/runtime.py": {"PlaywrightKorailBrowserClient", "probe_chromium"},
        },
        PLAYWRIGHT_RESULT_READER_MODULE: {
            "korail_browser_automation.py": PLAYWRIGHT_RESULT_READER_LEGACY_SYMBOLS,
            "korail_sidecar/playwright/client.py": PLAYWRIGHT_RESULT_READER_SYMBOLS,
            "korail_sidecar/playwright/search_form.py": {"_normalize_station"},
        },
        PLAYWRIGHT_SEARCH_FORM_MODULE: {
            "korail_sidecar/playwright/client.py": PLAYWRIGHT_SEARCH_FORM_FUNCTIONS,
        },
        PAGE_CONTRACT_MODULE: {
            "korail_browser_automation.py": PAGE_CONTRACT_SYMBOLS,
            "korail_browser_seat_source.py": {"OFFICIAL_KORAIL_SEARCH_URL"},
            "korail_pydoll_browser.py": PAGE_CONTRACT_SYMBOLS,
            "korail_sidecar/http.py": PAGE_CONTRACT_SYMBOLS,
            "korail_sidecar/playwright/client.py": PAGE_CONTRACT_SYMBOLS,
            "korail_sidecar/runtime.py": PAGE_CONTRACT_SYMBOLS,
            "timetable_management/korail_browser_projection.py": {"OFFICIAL_KORAIL_SEARCH_URL"},
        },
    }
    actual_consumers = {module: {} for module in expected_consumers}
    violations: list[str] = []
    facade_path = Path("korail_browser_automation.py")

    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative_path = path.relative_to(PACKAGE_ROOT)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if relative_path != facade_path:
            violations.extend(_legacy_references(source, Path("rail_waitlist") / relative_path))
        search_form_aliases = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module is None
            and path.parent.name == "playwright"
            for alias in node.names
            if alias.name == "search_form"
        }
        search_form_references = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in search_form_aliases
            and node.func.attr in PLAYWRIGHT_SEARCH_FORM_FUNCTIONS
        }
        if search_form_references:
            actual_consumers[PLAYWRIGHT_SEARCH_FORM_MODULE].setdefault(
                relative_path.as_posix(), set()
            ).update(search_form_references)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            resolved = _resolve_import(path, node)
            if resolved not in actual_consumers:
                continue
            imported = {alias.name for alias in node.names} & (
                COORDINATOR_SYMBOLS
                | PLAYWRIGHT_CLIENT_LEGACY_SYMBOLS
                | PLAYWRIGHT_RESULT_READER_SYMBOLS
                | PLAYWRIGHT_SEARCH_FORM_FUNCTIONS
                | PAGE_CONTRACT_SYMBOLS
            )
            if imported:
                actual_consumers[resolved].setdefault(relative_path.as_posix(), set()).update(
                    imported
                )

    if SCRIPT_ROOT.exists():
        for path in sorted(SCRIPT_ROOT.rglob("*.py")):
            relative_path = Path("scripts") / path.relative_to(SCRIPT_ROOT)
            violations.extend(_legacy_references(path.read_text(encoding="utf-8"), relative_path))

    assert violations == []
    assert actual_consumers == expected_consumers
