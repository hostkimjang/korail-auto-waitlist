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
from rail_waitlist import korail_pydoll_page_safety as legacy
from rail_waitlist.korail_sidecar.pydoll import page_safety as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
PUBLIC_SYMBOLS = {
    "AUTOMATION_GENERIC_PROTECTION_TRIGGERS",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "GENERIC_PROTECTION_TRIGGERS",
    "PydollPageSnapshot",
    "annotations",
    "assert_pydoll_response_allowed",
    "is_rate_limit_response",
    "logging",
    "protection_trigger_from_http_response",
    "protection_trigger_from_text",
}
PRIVATE_SYMBOLS = {"_log_protection_snapshot"}
LEGACY_PICKLES = {
    "assert_pydoll_response_allowed": (
        "gASVTgAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9wYWdlX3NhZmV0eZSM"
        "HmFzc2VydF9weWRvbGxfcmVzcG9uc2VfYWxsb3dlZJSTlC4="
    ),
    "_log_protection_snapshot": (
        "gASVSAAAAAAAAACMJ3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9wYWdlX3NhZmV0eZSM"
        "GF9sb2dfcHJvdGVjdGlvbl9zbmFwc2hvdJSTlC4="
    ),
}


def test_legacy_page_safety_is_an_assignment_only_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_pydoll_page_safety.py"
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
    assert imports == {("korail_sidecar.pydoll", 1, "page_safety", "_owner")}
    assert set(assignments) == PUBLIC_SYMBOLS | PRIVATE_SYMBOLS
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol
        assert getattr(legacy, symbol) is getattr(owner, symbol)


def test_owner_and_browser_keep_exact_page_safety_identities() -> None:
    assert owner.assert_pydoll_response_allowed.__module__ == owner.__name__
    assert owner._log_protection_snapshot.__module__ == owner.__name__
    assert owner.GENERIC_PROTECTION_TRIGGERS is owner.AUTOMATION_GENERIC_PROTECTION_TRIGGERS
    assert browser.assert_pydoll_response_allowed is owner.assert_pydoll_response_allowed
    assert browser._GENERIC_PROTECTION_TRIGGERS is owner.GENERIC_PROTECTION_TRIGGERS


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_pre_move_pickle_globals_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser"])
def test_import_orders_keep_one_owner_without_legacy_reentry(first_import: str) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.page_safety",
    "legacy": "rail_waitlist.korail_pydoll_page_safety",
    "browser": "rail_waitlist.korail_pydoll_browser",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_page_safety" in sys.modules
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_page_safety as legacy
from rail_waitlist.korail_sidecar.pydoll import page_safety as owner

print(json.dumps({
    "function_identity": (
        browser.assert_pydoll_response_allowed
        is legacy.assert_pydoll_response_allowed
        is owner.assert_pydoll_response_allowed
    ),
    "legacy_loaded_before": legacy_loaded_before,
    "module": owner.assert_pydoll_response_allowed.__module__,
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
        "function_identity": True,
        "legacy_loaded_before": first_import == "legacy",
        "module": "rail_waitlist.korail_sidecar.pydoll.page_safety",
    }


def test_owner_has_exact_import_boundary_and_one_production_consumer() -> None:
    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "page_safety.py"
    owner_tree = ast.parse(
        owner_path.read_text(encoding="utf-8"),
        filename=str(owner_path),
    )
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for alias in node.names
    }
    direct_imports = {
        alias.name
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    canonical_consumers: set[str] = set()
    legacy_consumers: set[str] = set()

    for path in SOURCE_ROOT.rglob("*.py"):
        relative_path = path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == "korail_sidecar.pydoll.page_safety":
                canonical_consumers.add(relative_path)
            if (
                relative_path == "korail_sidecar/pydoll/search_snapshot_policy.py"
                and node.level == 1
                and node.module == "page_safety"
            ):
                canonical_consumers.add(relative_path)
            if node.module == "korail_pydoll_page_safety":
                legacy_consumers.add(relative_path)

    assert direct_imports == {"logging"}
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("browser_contracts", 2, "BrowserProtectionDetected", None),
        ("browser_contracts", 2, "BrowserRateLimited", None),
        ("browser_contracts", 2, "ProtectionTrigger", None),
        (
            "browser_protection",
            2,
            "GENERIC_PROTECTION_TRIGGERS",
            "BROWSER_GENERIC_PROTECTION_TRIGGERS",
        ),
        ("browser_protection", 2, "is_rate_limit_response", None),
        ("browser_protection", 2, "protection_trigger_from_http_response", None),
        ("browser_protection", 2, "protection_trigger_from_text", None),
        ("dataclasses", 0, "dataclass", None),
        ("page_contracts", 1, "PydollPageSnapshot", None),
        ("typing", 0, "Literal", None),
    }
    assert canonical_consumers == {
        "korail_pydoll_browser.py",
        "korail_sidecar/pydoll/search_snapshot_policy.py",
    }
    assert legacy_consumers == set()
