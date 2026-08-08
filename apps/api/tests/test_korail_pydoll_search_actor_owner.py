from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path
from typing import TypeAliasType, get_origin

import pytest

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_search_actor as legacy
from rail_waitlist.korail_sidecar.pydoll import search_actor as owner

API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"

PUBLIC_SYMBOLS = {
    "Awaitable",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSeatSearchRequest",
    "BrowserSeatSearchResult",
    "BrowserSourceUnavailable",
    "BrowserTrainSnapshot",
    "Callable",
    "Cleanup",
    "KORAIL_ROUTE_HEADING",
    "KorailHttpReplayClientFactory",
    "KorailHttpReplayPlan",
    "KorailPydollReadOnlySearchSession",
    "KorailPydollReadOnlySearchSessionContext",
    "KorailPydollReadOnlySearchSessionFactory",
    "KorailStationIdentityResolver",
    "KorailStationIdentityUnavailable",
    "Mapping",
    "Protocol",
    "PydollHttpReplayManager",
    "PydollPageSnapshot",
    "PydollReadOnlySearchActor",
    "ResponseSafetyGuard",
    "UTC",
    "annotations",
    "asyncio",
    "build_korail_general_search_url",
    "clock_time",
    "dataclass",
    "date",
    "datetime",
    "logging",
    "normalize_korail_station",
    "normalize_korail_train_number",
    "parse_expected_delay_minutes",
    "parse_official_train_type",
    "parse_unambiguous_adult_fare",
    "service_datetimes",
    "status_from_seat_box",
    "sys",
}
PRIVATE_SYMBOLS = {
    "_ActiveReadOnlySearchSession",
    "_MAX_MORE_RESULT_ACTIONS",
    "_ReadOnlySearchSessionLease",
}
OWNER_DEFINITIONS = {
    "KorailPydollReadOnlySearchSession",
    "KorailPydollReadOnlySearchSessionContext",
    "PydollReadOnlySearchActor",
    "_ActiveReadOnlySearchSession",
    "_ReadOnlySearchSessionLease",
}
RUNTIME_ALIASES = {
    "Cleanup",
    "KorailPydollReadOnlySearchSessionFactory",
    "ResponseSafetyGuard",
}
LEGACY_DEFINITION_PICKLES = {
    "KorailPydollReadOnlySearchSession": (
        "gASVUgAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfYWN0b3KU"
        "jCFLb3JhaWxQeWRvbGxSZWFkT25seVNlYXJjaFNlc3Npb26Uk5Qu"
    ),
    "KorailPydollReadOnlySearchSessionContext": (
        "gASVWQAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfYWN0b3KU"
        "jChLb3JhaWxQeWRvbGxSZWFkT25seVNlYXJjaFNlc3Npb25Db250ZXh0lJOULg=="
    ),
    "_ActiveReadOnlySearchSession": (
        "gASVTQAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfYWN0b3KU"
        "jBxfQWN0aXZlUmVhZE9ubHlTZWFyY2hTZXNzaW9ulJOULg=="
    ),
    "_ReadOnlySearchSessionLease": (
        "gASVTAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfYWN0b3KU"
        "jBtfUmVhZE9ubHlTZWFyY2hTZXNzaW9uTGVhc2WUk5Qu"
    ),
    "PydollReadOnlySearchActor": (
        "gASVSgAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfYWN0b3KU"
        "jBlQeWRvbGxSZWFkT25seVNlYXJjaEFjdG9ylJOULg=="
    ),
}
LEGACY_ALIAS_PICKLES = {
    "ResponseSafetyGuard": (
        "gASVpgAAAAAAAACMD2NvbGxlY3Rpb25zLmFiY5SMFV9DYWxsYWJsZUdlbmVyaWNBbGlhc5ST"
        "lGgAjAhDYWxsYWJsZZSTlF2UKIwycmFpbF93YWl0bGlzdC5rb3JhaWxfc2lkZWNhci5weWRv"
        "bGwucGFnZV9jb250cmFjdHOUjBJQeWRvbGxQYWdlU25hcHNob3SUk5SMCGJ1aWx0aW5zlIwD"
        "c3RylJOUZU6GlIaUUpQu"
    ),
    "Cleanup": (
        "gASVlQAAAAAAAACMD2NvbGxlY3Rpb25zLmFiY5SMFV9DYWxsYWJsZUdlbmVyaWNBbGlhc5ST"
        "lGgAjAhDYWxsYWJsZZSTlF2UjAV0eXBlc5SMDEdlbmVyaWNBbGlhc5STlGgAjAlBd2FpdGFi"
        "bGWUk5SMCGJ1aWx0aW5zlIwGb2JqZWN0lJOUhZSGlFKUYWgIaApOhZSGlFKUhpSGlFKULg=="
    ),
    "KorailPydollReadOnlySearchSessionFactory": (
        "gASVxgAAAAAAAACMD2NvbGxlY3Rpb25zLmFiY5SMFV9DYWxsYWJsZUdlbmVyaWNBbGlhc5ST"
        "lGgAjAhDYWxsYWJsZZSTlF2UKIwIYnVpbHRpbnOUjANzdHKUk5RoBowDaW50lJOUaAaMBGJv"
        "b2yUk5RljChyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxfc2VhcmNoX2FjdG9ylIwoS29y"
        "YWlsUHlkb2xsUmVhZE9ubHlTZWFyY2hTZXNzaW9uQ29udGV4dJSTlIaUhpRSlC4="
    ),
}


def test_legacy_search_actor_is_a_definition_free_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_pydoll_search_actor.py"
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
        ("korail_sidecar.pydoll", 1, "search_actor", "_owner"),
        ("korail_sidecar.pydoll.search_actor", 1, "Callable", "Callable"),
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


def test_search_actor_definitions_and_runtime_aliases_have_one_owner() -> None:
    for symbol in OWNER_DEFINITIONS:
        value = getattr(owner, symbol)
        assert value.__module__ == owner.__name__
        assert getattr(legacy, symbol) is value

    for symbol in RUNTIME_ALIASES:
        value = getattr(owner, symbol)
        assert getattr(legacy, symbol) is value
        assert get_origin(value) is owner.Callable
        assert not isinstance(value, TypeAliasType)

    assert owner._MAX_MORE_RESULT_ACTIONS == 19
    assert legacy._MAX_MORE_RESULT_ACTIONS == 19
    assert browser.PydollReadOnlySearchActor is owner.PydollReadOnlySearchActor


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_DEFINITION_PICKLES.items())
def test_pre_move_search_actor_definition_pickles_restore_by_identity(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_ALIAS_PICKLES.items())
def test_pre_move_search_actor_alias_pickles_restore_by_type_and_equality(
    symbol: str,
    payload: str,
) -> None:
    restored = pickle.loads(base64.b64decode(payload))
    current = getattr(owner, symbol)
    assert type(restored) is type(current)
    assert restored == current


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser"])
def test_search_actor_import_orders_are_passive_and_avoid_legacy_reentry(
    first_import: str,
) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.search_actor",
    "legacy": "rail_waitlist.korail_pydoll_search_actor",
    "browser": "rail_waitlist.korail_pydoll_browser",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_search_actor" in sys.modules
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_search_actor as legacy
from rail_waitlist.korail_sidecar.pydoll import search_actor as owner

print(json.dumps({
    "backend_loaded": sorted(
        name for name in sys.modules if name == "pydoll" or name.startswith("pydoll.")
    ),
    "identity": (
        browser.PydollReadOnlySearchActor
        is legacy.PydollReadOnlySearchActor
        is owner.PydollReadOnlySearchActor
    ),
    "legacy_loaded_before": legacy_loaded_before,
    "modules": sorted({
        owner.KorailPydollReadOnlySearchSession.__module__,
        owner._ActiveReadOnlySearchSession.__module__,
        owner.PydollReadOnlySearchActor.__module__,
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
        "backend_loaded": [],
        "identity": True,
        "legacy_loaded_before": first_import == "legacy",
        "modules": ["rail_waitlist.korail_sidecar.pydoll.search_actor"],
    }


def test_search_actor_has_one_canonical_consumer_and_no_legacy_reentry() -> None:
    canonical_consumers: set[str] = set()
    legacy_consumers: set[str] = set()
    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_actor.py"
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
                if node.module.endswith("korail_sidecar.pydoll.search_actor"):
                    if path != SOURCE_ROOT / "korail_pydoll_search_actor.py":
                        canonical_consumers.add(relative_path)
                elif node.module.endswith("korail_pydoll_search_actor"):
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
            "korail_pydoll_auth_actor",
            "korail_pydoll_browser",
            "korail_pydoll_reservation_actor",
            "korail_sidecar.pydoll.reservation_actor",
            "korail_pydoll_search_actor",
        }
    )
