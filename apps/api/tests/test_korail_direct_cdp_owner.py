from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_browser_automation
from rail_waitlist import korail_chromium_launch as legacy_launch
from rail_waitlist import korail_direct_cdp as legacy_cdp
from rail_waitlist.korail_sidecar import chromium_launch as launch_owner
from rail_waitlist.korail_sidecar import direct_cdp as cdp_owner

API_ROOT = Path(__file__).resolve().parents[1]
CDP_MODULE = "rail_waitlist.korail_sidecar.direct_cdp"
LAUNCH_MODULE = "rail_waitlist.korail_sidecar.chromium_launch"
LEGACY_DIRECT_CDP_ERROR_PICKLE = (
    "gASVZAAAAAAAAACMH3JhaWxfd2FpdGxpc3Qua29yYWlsX2RpcmVjdF9jZHCU"
    "jBREaXJlY3RDZHBMYXVuY2hFcnJvcpSTlIwhZGlyZWN0IENocm9taXVtIENE"
    "UCBsYXVuY2ggZmFpbGVklIWUUpQu"
)


def test_legacy_browser_lifecycle_modules_have_no_runtime_definitions() -> None:
    for relative_path in ("korail_direct_cdp.py", "korail_chromium_launch.py"):
        module_path = API_ROOT / "src" / "rail_waitlist" / relative_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

        assert not any(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            for node in tree.body
        )


def test_legacy_browser_lifecycle_symbols_are_exact_canonical_aliases() -> None:
    for symbol in (
        "asyncio",
        "logging",
        "os",
        "tempfile",
        "time",
        "AsyncIterator",
        "asynccontextmanager",
        "Path",
        "Protocol",
        "annotations",
        "ChromiumBrowserType",
        "DirectCdpLaunchError",
        "_chromium_environment",
        "_cleanup_browser_process",
        "_stop_process",
        "_wait_for_debugging_port",
        "isolated_test_chromium_arguments",
        "logger",
        "open_direct_cdp_browser",
    ):
        assert getattr(legacy_cdp, symbol) is getattr(cdp_owner, symbol)

    assert legacy_launch.os is launch_owner.os
    assert legacy_launch._TEST_DISABLE_SANDBOX_ENV is launch_owner._TEST_DISABLE_SANDBOX_ENV
    assert (
        legacy_launch.isolated_test_chromium_arguments
        is launch_owner.isolated_test_chromium_arguments
    )
    assert (
        cdp_owner.isolated_test_chromium_arguments is launch_owner.isolated_test_chromium_arguments
    )


def test_legacy_browser_lifecycle_wildcard_surfaces_are_preserved() -> None:
    direct_public = {name for name in vars(legacy_cdp) if not name.startswith("_")}
    launch_public = {name for name in vars(legacy_launch) if not name.startswith("_")}

    assert direct_public == {
        "AsyncIterator",
        "ChromiumBrowserType",
        "DirectCdpLaunchError",
        "Path",
        "Protocol",
        "annotations",
        "asynccontextmanager",
        "asyncio",
        "isolated_test_chromium_arguments",
        "logger",
        "logging",
        "open_direct_cdp_browser",
        "os",
        "tempfile",
        "time",
    }
    assert launch_public == {"annotations", "isolated_test_chromium_arguments", "os"}


def test_canonical_browser_lifecycle_identity_and_consumer_binding() -> None:
    assert cdp_owner.ChromiumBrowserType.__module__ == CDP_MODULE
    assert cdp_owner.DirectCdpLaunchError.__module__ == CDP_MODULE
    assert cdp_owner.open_direct_cdp_browser.__module__ == CDP_MODULE
    assert launch_owner.isolated_test_chromium_arguments.__module__ == LAUNCH_MODULE
    assert cdp_owner.logger.name == "rail_waitlist.korail_direct_cdp"
    assert korail_browser_automation.DirectCdpLaunchError is cdp_owner.DirectCdpLaunchError
    assert korail_browser_automation.open_direct_cdp_browser is cdp_owner.open_direct_cdp_browser


def test_pre_move_exception_pickle_restores_canonical_error() -> None:
    restored = pickle.loads(base64.b64decode(LEGACY_DIRECT_CDP_ERROR_PICKLE))

    assert type(restored) is cdp_owner.DirectCdpLaunchError
    assert restored.args == ("direct Chromium CDP launch failed",)


def test_legacy_private_reassignment_does_not_mutate_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_stop = cdp_owner._stop_process

    monkeypatch.setattr(legacy_cdp, "_stop_process", object())

    assert cdp_owner._stop_process is canonical_stop


@pytest.mark.parametrize(
    ("import_order", "legacy_cdp_loaded", "legacy_launch_loaded"),
    [
        ("canonical-first", False, False),
        ("legacy-cdp-first", True, False),
        ("legacy-launch-first", False, True),
        ("consumer-first", False, False),
    ],
)
def test_browser_lifecycle_import_orders_keep_one_owner(
    import_order: str,
    legacy_cdp_loaded: bool,
    legacy_launch_loaded: bool,
) -> None:
    script = r"""
import json
import sys

order = sys.argv[1]
if order == "canonical-first":
    from rail_waitlist.korail_sidecar import direct_cdp as first
elif order == "legacy-cdp-first":
    from rail_waitlist import korail_direct_cdp as first
elif order == "legacy-launch-first":
    from rail_waitlist import korail_chromium_launch as first
else:
    from rail_waitlist import korail_browser_automation as first

legacy_cdp_loaded = "rail_waitlist.korail_direct_cdp" in sys.modules
legacy_launch_loaded = "rail_waitlist.korail_chromium_launch" in sys.modules
from rail_waitlist import korail_browser_automation
from rail_waitlist import korail_chromium_launch as legacy_launch
from rail_waitlist import korail_direct_cdp as legacy_cdp
from rail_waitlist.korail_sidecar import chromium_launch as launch_owner
from rail_waitlist.korail_sidecar import direct_cdp as cdp_owner

print(json.dumps({
    "cdp_identity": all([
        legacy_cdp.ChromiumBrowserType is cdp_owner.ChromiumBrowserType,
        legacy_cdp.DirectCdpLaunchError is cdp_owner.DirectCdpLaunchError,
        legacy_cdp.open_direct_cdp_browser is cdp_owner.open_direct_cdp_browser,
    ]),
    "launch_identity": (
        legacy_launch.isolated_test_chromium_arguments
        is launch_owner.isolated_test_chromium_arguments
    ),
    "consumer_identity": all([
        korail_browser_automation.DirectCdpLaunchError is cdp_owner.DirectCdpLaunchError,
        korail_browser_automation.open_direct_cdp_browser
        is cdp_owner.open_direct_cdp_browser,
    ]),
    "legacy_cdp_loaded": legacy_cdp_loaded,
    "legacy_launch_loaded": legacy_launch_loaded,
    "error_module": cdp_owner.DirectCdpLaunchError.__module__,
    "launch_module": launch_owner.isolated_test_chromium_arguments.__module__,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "cdp_identity": True,
        "consumer_identity": True,
        "error_module": CDP_MODULE,
        "launch_identity": True,
        "launch_module": LAUNCH_MODULE,
        "legacy_cdp_loaded": legacy_cdp_loaded,
        "legacy_launch_loaded": legacy_launch_loaded,
    }
