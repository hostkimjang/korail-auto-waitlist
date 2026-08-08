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
from rail_waitlist import korail_pydoll_http_replay as legacy
from rail_waitlist import korail_pydoll_search_actor as search_actor
from rail_waitlist.korail_sidecar.pydoll import http_replay as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
PUBLIC_SYMBOLS = {
    "Awaitable",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSeatSearchRequest",
    "BrowserSeatSearchResult",
    "BrowserSourceUnavailable",
    "Callable",
    "Cleanup",
    "DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE",
    "HttpReplayInvalidCapture",
    "HttpReplayInvalidResponse",
    "HttpReplayLeaseInvalid",
    "HttpReplayProtectionDetected",
    "HttpReplayRateLimited",
    "HttpReplaySessionInvalid",
    "HttpReplaySourceUnavailable",
    "KorailHttpReplayCaptureSession",
    "KorailHttpReplayClientFactory",
    "KorailHttpReplayPlan",
    "KorailHttpReplaySearchClient",
    "Mapping",
    "MappingProxyType",
    "OrderedDict",
    "Protocol",
    "PydollHttpReplayManager",
    "annotations",
    "asyncio",
    "dataclass",
    "date",
    "logger",
    "logging",
    "normalize_replay_protection_trigger",
}
PRIVATE_SYMBOLS = {"_ActiveHttpReplayLease", "_RouteKey"}
OWNER_DEFINITIONS = {
    "KorailHttpReplayCaptureSession",
    "KorailHttpReplayClientFactory",
    "KorailHttpReplaySearchClient",
    "PydollHttpReplayManager",
    "_ActiveHttpReplayLease",
}
LEGACY_PICKLES = {
    "KorailHttpReplayCaptureSession": (
        "gASVTgAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9odHRwX3JlcGxheZSM"
        "HktvcmFpbEh0dHBSZXBsYXlDYXB0dXJlU2Vzc2lvbpSTlC4="
    ),
    "PydollHttpReplayManager": (
        "gASVRwAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9odHRwX3JlcGxheZSM"
        "F1B5ZG9sbEh0dHBSZXBsYXlNYW5hZ2VylJOULg=="
    ),
    "_ActiveHttpReplayLease": (
        "gASVRgAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9odHRwX3JlcGxheZSM"
        "Fl9BY3RpdmVIdHRwUmVwbGF5TGVhc2WUk5Qu"
    ),
}


def test_legacy_http_replay_manager_is_an_assignment_only_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_pydoll_http_replay.py"
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
    assert imports == {("korail_sidecar.pydoll", 1, "http_replay", "_owner")}
    assert set(assignments) == PUBLIC_SYMBOLS | PRIVATE_SYMBOLS
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol
        assert getattr(legacy, symbol) is getattr(owner, symbol)


def test_http_replay_manager_definitions_have_one_canonical_owner() -> None:
    for symbol in OWNER_DEFINITIONS:
        value = getattr(owner, symbol)
        assert value.__module__ == owner.__name__
        assert getattr(legacy, symbol) is value

    assert search_actor.PydollHttpReplayManager is owner.PydollHttpReplayManager
    assert search_actor.KorailHttpReplayClientFactory is owner.KorailHttpReplayClientFactory
    assert (
        browser.DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE == owner.DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE
    )
    assert owner.logger is legacy.logger
    assert owner.logger.name == "rail_waitlist.korail_pydoll_http_replay"


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_pre_move_http_replay_manager_pickles_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser", "search_actor"])
def test_http_replay_manager_import_orders_keep_one_owner(first_import: str) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.http_replay",
    "legacy": "rail_waitlist.korail_pydoll_http_replay",
    "browser": "rail_waitlist.korail_pydoll_browser",
    "search_actor": "rail_waitlist.korail_pydoll_search_actor",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_http_replay" in sys.modules
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_http_replay as legacy
from rail_waitlist import korail_pydoll_search_actor as search_actor
from rail_waitlist.korail_sidecar.pydoll import http_replay as owner

print(json.dumps({
    "identity": (
        search_actor.PydollHttpReplayManager
        is legacy.PydollHttpReplayManager
        is owner.PydollHttpReplayManager
    ),
    "legacy_loaded_before": legacy_loaded_before,
    "logger": owner.logger.name,
    "module": owner.PydollHttpReplayManager.__module__,
    "route_cache_size": (
        browser.DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE
        == owner.DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE
    ),
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
        "logger": "rail_waitlist.korail_pydoll_http_replay",
        "module": "rail_waitlist.korail_sidecar.pydoll.http_replay",
        "route_cache_size": True,
    }


def test_http_replay_manager_has_exact_canonical_consumers_and_no_reverse_dependency() -> None:
    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "http_replay.py"
    owner_tree = ast.parse(
        owner_path.read_text(encoding="utf-8"),
        filename=str(owner_path),
    )
    imported_modules = {
        node.module
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    canonical_consumers: set[str] = set()

    for path in SOURCE_ROOT.rglob("*.py"):
        relative_path = path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (
                node.module == "korail_sidecar.pydoll.http_replay"
                or (
                    relative_path == "korail_sidecar/pydoll/search_actor.py"
                    and node.level == 1
                    and node.module == "http_replay"
                )
            ):
                canonical_consumers.add(relative_path)

    assert "korail_pydoll_browser" not in imported_modules
    assert "korail_pydoll_search_actor" not in imported_modules
    assert canonical_consumers == {
        "korail_pydoll_browser.py",
        "korail_sidecar/pydoll/search_actor.py",
    }
