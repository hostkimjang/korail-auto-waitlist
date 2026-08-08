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
from rail_waitlist import korail_pydoll_login_driver as legacy
from rail_waitlist.korail_sidecar.pydoll import login_driver as owner

API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"

PUBLIC_SYMBOLS = {
    "Any",
    "Awaitable",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSourceUnavailable",
    "Callable",
    "ExactTextWaiter",
    "ExactVisibleReader",
    "KorailCredentialInput",
    "KorailLoginMethod",
    "LoginAttemptState",
    "LoginExecuteScript",
    "LoginGoTo",
    "LoginWorkflowCompatibilityPort",
    "Mapping",
    "Protocol",
    "PydollLoginDomDriver",
    "PydollPageSnapshot",
    "ResponseSafetyGuard",
    "SnapshotReader",
    "VisibleElements",
    "annotations",
    "dataclass",
    "logging",
    "login_step",
}
PRIVATE_SYMBOLS = {"_LocalLoginAttemptState"}
OWNER_DEFINITIONS = {
    "ExactTextWaiter",
    "ExactVisibleReader",
    "LoginAttemptState",
    "LoginExecuteScript",
    "LoginGoTo",
    "LoginWorkflowCompatibilityPort",
    "PydollLoginDomDriver",
    "ResponseSafetyGuard",
    "SnapshotReader",
    "VisibleElements",
    "_LocalLoginAttemptState",
    "login_step",
}
OWNER_TYPE_ALIASES = {
    "ExactTextWaiter",
    "ExactVisibleReader",
    "ResponseSafetyGuard",
    "SnapshotReader",
}
LEGACY_PICKLES = {
    "LoginAttemptState": (
        "gASVQgAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jBFMb2dpbkF0dGVtcHRTdGF0ZZSTlC4="
    ),
    "_LocalLoginAttemptState": (
        "gASVSAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jBdfTG9jYWxMb2dpbkF0dGVtcHRTdGF0ZZSTlC4="
    ),
    "LoginWorkflowCompatibilityPort": (
        "gASVTwAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jB5Mb2dpbldvcmtmbG93Q29tcGF0aWJpbGl0eVBvcnSUk5Qu"
    ),
    "LoginGoTo": (
        "gASVOgAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jAlMb2dpbkdvVG+Uk5Qu"
    ),
    "LoginExecuteScript": (
        "gASVQwAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jBJMb2dpbkV4ZWN1dGVTY3JpcHSUk5Qu"
    ),
    "VisibleElements": (
        "gASVQAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jA9WaXNpYmxlRWxlbWVudHOUk5Qu"
    ),
    "SnapshotReader": (
        "gASVPwAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jA5TbmFwc2hvdFJlYWRlcpSTlC4="
    ),
    "ExactVisibleReader": (
        "gASVQwAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jBJFeGFjdFZpc2libGVSZWFkZXKUk5Qu"
    ),
    "ExactTextWaiter": (
        "gASVQAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jA9FeGFjdFRleHRXYWl0ZXKUk5Qu"
    ),
    "ResponseSafetyGuard": (
        "gASVRAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jBNSZXNwb25zZVNhZmV0eUd1YXJklJOULg=="
    ),
    "login_step": (
        "gASVOwAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jApsb2dpbl9zdGVwlJOULg=="
    ),
    "PydollLoginDomDriver": (
        "gASVRQAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9sb2dpbl9kcml2ZXKU"
        "jBRQeWRvbGxMb2dpbkRvbURyaXZlcpSTlC4="
    ),
}


def test_legacy_login_driver_is_a_definition_free_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_pydoll_login_driver.py"
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
        ("korail_sidecar.pydoll", 1, "login_driver", "_owner"),
        ("korail_sidecar.pydoll.login_driver", 1, "Callable", "Callable"),
    }
    assert set(assignments) == (PUBLIC_SYMBOLS - {"Callable"}) | PRIVATE_SYMBOLS
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    private_names = {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    }
    assert private_names == PRIVATE_SYMBOLS
    assert set(owner.__all__) == PUBLIC_SYMBOLS
    assert not hasattr(legacy, "__all__")
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol
        assert getattr(legacy, symbol) is getattr(owner, symbol)
    assert legacy.Callable is owner.Callable


def test_login_driver_definitions_have_one_canonical_owner() -> None:
    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "login_driver.py"
    tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
    type_aliases = {node.name.id for node in tree.body if isinstance(node, ast.TypeAlias)}

    assert type_aliases == OWNER_TYPE_ALIASES
    for symbol in OWNER_DEFINITIONS:
        value = getattr(owner, symbol)
        assert value.__module__ == owner.__name__
        assert getattr(legacy, symbol) is value

    assert browser.LoginAttemptState is owner.LoginAttemptState
    assert browser.PydollLoginDomDriver is owner.PydollLoginDomDriver
    assert browser.login_step is owner.login_step


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_pre_move_login_driver_pickles_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser"])
def test_login_driver_import_orders_are_passive_and_avoid_legacy_reentry(
    first_import: str,
) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.login_driver",
    "legacy": "rail_waitlist.korail_pydoll_login_driver",
    "browser": "rail_waitlist.korail_pydoll_browser",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_login_driver" in sys.modules
backend_loaded = sorted(
    name for name in sys.modules if name == "pydoll" or name.startswith("pydoll.")
)
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_login_driver as legacy
from rail_waitlist.korail_sidecar.pydoll import login_driver as owner

print(json.dumps({
    "backend_loaded": backend_loaded,
    "identity": all((
        browser.LoginAttemptState is owner.LoginAttemptState,
        browser.PydollLoginDomDriver is legacy.PydollLoginDomDriver,
        browser.PydollLoginDomDriver is owner.PydollLoginDomDriver,
        browser.login_step is owner.login_step,
    )),
    "legacy_loaded_before": legacy_loaded_before,
    "modules": sorted({
        owner.LoginAttemptState.__module__,
        owner.SnapshotReader.__module__,
        owner.login_step.__module__,
        owner.PydollLoginDomDriver.__module__,
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
        "modules": ["rail_waitlist.korail_sidecar.pydoll.login_driver"],
    }


def test_login_driver_has_one_canonical_consumer_and_no_legacy_reentry() -> None:
    canonical_consumers: set[str] = set()
    legacy_consumers: set[str] = set()
    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "login_driver.py"
    owner_tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))

    scan_roots = (SOURCE_ROOT, API_ROOT / "scripts", REPOSITORY_ROOT / "scripts")
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            referenced_modules: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    referenced_modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    referenced_modules.add(node.module)
                    if node.module == "rail_waitlist":
                        referenced_modules.update(
                            f"rail_waitlist.{alias.name}" for alias in node.names
                        )
                elif (
                    isinstance(node, ast.Call)
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and (
                        isinstance(node.func, ast.Name)
                        and node.func.id == "__import__"
                        or isinstance(node.func, ast.Attribute)
                        and node.func.attr == "import_module"
                    )
                ):
                    referenced_modules.add(node.args[0].value)

            for module_name in referenced_modules:
                if module_name.endswith("korail_sidecar.pydoll.login_driver"):
                    if path != SOURCE_ROOT / "korail_pydoll_login_driver.py":
                        canonical_consumers.add(relative_path)
                elif module_name.endswith("korail_pydoll_login_driver"):
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
            "korail_pydoll_login_driver",
            "korail_pydoll_reservation_actor",
            "korail_sidecar.pydoll.reservation_actor",
        }
    )
