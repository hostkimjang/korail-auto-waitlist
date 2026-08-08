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
from rail_waitlist import korail_pydoll_search_driver as legacy
from rail_waitlist.korail_sidecar.pydoll import search_driver as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"

PUBLIC_SYMBOLS = {
    "Any",
    "Awaitable",
    "BrowserSourceUnavailable",
    "Callable",
    "Collection",
    "EvaluateText",
    "EvaluateValue",
    "ExecuteScript",
    "Mapping",
    "Protocol",
    "PydollPageSnapshot",
    "PydollSearchDomDriver",
    "PydollSeatBox",
    "PydollTrainRow",
    "QueryElement",
    "SearchControlState",
    "SearchDomCompatibilityPort",
    "SearchHourCandidate",
    "SnapshotMerge",
    "SnapshotStop",
    "SnapshotTransform",
    "TrainRowIdentity",
    "advance_search_expansion",
    "annotations",
    "begin_search_expansion",
    "dataclass",
    "date",
    "protection_trigger_from_text",
    "re",
}
OWNER_DEFINITIONS = {
    "EvaluateText",
    "EvaluateValue",
    "ExecuteScript",
    "PydollSearchDomDriver",
    "QueryElement",
    "SearchControlState",
    "SearchDomCompatibilityPort",
    "SearchHourCandidate",
    "SnapshotMerge",
    "SnapshotStop",
    "SnapshotTransform",
    "TrainRowIdentity",
}
LEGACY_PICKLES = {
    "SearchControlState": (
        "gASVRAAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwSU2VhcmNoQ29udHJvbFN0YXRllJOULg=="
    ),
    "SearchHourCandidate": (
        "gASVRQAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwTU2VhcmNoSG91ckNhbmRpZGF0ZZSTlC4="
    ),
    "SearchDomCompatibilityPort": (
        "gASVTAAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwaU2VhcmNoRG9tQ29tcGF0aWJpbGl0eVBvcnSUk5Qu"
    ),
    "QueryElement": (
        "gASVPgAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwMUXVlcnlFbGVtZW50lJOULg=="
    ),
    "ExecuteScript": (
        "gASVPwAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwNRXhlY3V0ZVNjcmlwdJSTlC4="
    ),
    "EvaluateValue": (
        "gASVPwAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwNRXZhbHVhdGVWYWx1ZZSTlC4="
    ),
    "EvaluateText": (
        "gASVPgAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwMRXZhbHVhdGVUZXh0lJOULg=="
    ),
    "SnapshotTransform": (
        "gASVQwAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwRU25hcHNob3RUcmFuc2Zvcm2Uk5Qu"
    ),
    "SnapshotMerge": (
        "gASVPwAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwNU25hcHNob3RNZXJnZZSTlC4="
    ),
    "SnapshotStop": (
        "gASVPgAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwMU25hcHNob3RTdG9wlJOULg=="
    ),
    "TrainRowIdentity": (
        "gASVQgAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwQVHJhaW5Sb3dJZGVudGl0eZSTlC4="
    ),
    "PydollSearchDomDriver": (
        "gASVRwAAAAAAAACMKXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9zZWFyY2hfZHJpdmVy"
        "lIwVUHlkb2xsU2VhcmNoRG9tRHJpdmVylJOULg=="
    ),
}


def test_legacy_search_driver_is_a_definition_free_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_pydoll_search_driver.py"
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
        ("korail_sidecar.pydoll", 1, "search_driver", "_owner"),
        ("korail_sidecar.pydoll.search_driver", 1, "Callable", "Callable"),
    }
    assert set(assignments) == PUBLIC_SYMBOLS - {"Callable"}
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    private_names = {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    }
    assert private_names == set()
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol
        assert getattr(legacy, symbol) is getattr(owner, symbol)
    assert legacy.Callable is owner.Callable


def test_search_driver_definitions_have_one_canonical_owner() -> None:
    for symbol in OWNER_DEFINITIONS:
        value = getattr(owner, symbol)
        assert value.__module__ == owner.__name__
        assert getattr(legacy, symbol) is value

    assert browser.PydollSearchDomDriver is owner.PydollSearchDomDriver
    assert browser.SearchControlState is owner.SearchControlState
    assert browser._HourCandidate is owner.SearchHourCandidate


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_pre_move_search_driver_pickles_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser"])
def test_search_driver_import_orders_keep_one_owner_without_legacy_reentry(
    first_import: str,
) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.search_driver",
    "legacy": "rail_waitlist.korail_pydoll_search_driver",
    "browser": "rail_waitlist.korail_pydoll_browser",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_search_driver" in sys.modules
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_search_driver as legacy
from rail_waitlist.korail_sidecar.pydoll import search_driver as owner

print(json.dumps({
    "identity": all((
        browser.PydollSearchDomDriver is legacy.PydollSearchDomDriver,
        browser.PydollSearchDomDriver is owner.PydollSearchDomDriver,
        browser.SearchControlState is owner.SearchControlState,
        browser._HourCandidate is owner.SearchHourCandidate,
    )),
    "legacy_loaded_before": legacy_loaded_before,
    "modules": sorted({
        owner.SearchControlState.__module__,
        owner.SearchHourCandidate.__module__,
        owner.PydollSearchDomDriver.__module__,
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
        "modules": ["rail_waitlist.korail_sidecar.pydoll.search_driver"],
    }


def test_search_driver_has_one_canonical_production_consumer_and_no_legacy_reentry() -> None:
    canonical_consumers: set[str] = set()
    legacy_consumers: set[str] = set()
    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_driver.py"
    owner_tree = ast.parse(
        owner_path.read_text(encoding="utf-8"),
        filename=str(owner_path),
    )

    for path in SOURCE_ROOT.rglob("*.py"):
        relative_path = path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if (
                node.module == "korail_sidecar.pydoll.search_driver"
                and relative_path != "korail_pydoll_search_driver.py"
            ):
                canonical_consumers.add(relative_path)
            elif node.module == "korail_pydoll_search_driver":
                legacy_consumers.add(relative_path)

    owner_imports = {
        node.module
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert canonical_consumers == {"korail_pydoll_browser.py"}
    assert legacy_consumers == set()
    assert owner_imports.isdisjoint(
        {
            "korail_pydoll_browser",
            "korail_pydoll_search_actor",
            "korail_pydoll_search_driver",
        }
    )
