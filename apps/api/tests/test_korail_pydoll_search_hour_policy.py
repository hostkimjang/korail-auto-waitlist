from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import search_hour_policy as policy
from rail_waitlist.korail_sidecar.pydoll.search_driver import SearchHourCandidate

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_hour_policy.py"
BROWSER_PATH = SOURCE_ROOT / "korail_pydoll_browser.py"
OWNER_MODULE = "rail_waitlist.korail_sidecar.pydoll.search_hour_policy"

OWNER_SYMBOLS = (
    "control_state_log_value",
    "current_hour_window",
    "has_disabled_class",
    "hour_window_signature",
    "is_exact_hour_catalog",
    "is_exact_selected_hour",
    "is_soft_adjacent_hour",
    "is_soft_aria_hour",
    "is_soft_dom_hour",
)
BROWSER_ALIASES = {
    "_control_state_log_value": "control_state_log_value",
    "_current_hour_window": "current_hour_window",
    "_hour_window_signature": "hour_window_signature",
    "_is_exact_hour_catalog": "is_exact_hour_catalog",
    "_is_exact_selected_hour": "is_exact_selected_hour",
    "_is_soft_adjacent_hour": "is_soft_adjacent_hour",
    "_is_soft_aria_hour": "is_soft_aria_hour",
    "_is_soft_dom_hour": "is_soft_dom_hour",
}
LEGACY_PICKLES = {
    "_current_hour_window": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxf"
        "YnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2N1cnJlbnRfaG91cl93aW5kb3cKcDEKdFJwMgou"
    ),
    "_hour_window_signature": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxf"
        "YnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpwMQpWX2hvdXJfd2luZG93X3NpZ25hdHVyZQpwMgp0"
        "cDMKUnA0Ci4="
    ),
    "_is_soft_aria_hour": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxf"
        "YnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2lzX3NvZnRfYXJpYV9ob3VyCnAxCnRScDIKLg=="
    ),
    "_is_soft_dom_hour": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxf"
        "YnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2lzX3NvZnRfZG9tX2hvdXIKcDEKdFJwMgou"
    ),
    "_is_exact_hour_catalog": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxf"
        "YnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2lzX2V4YWN0X2hvdXJfY2F0YWxvZwpwMQp0UnAy"
        "Ci4="
    ),
    "_is_soft_adjacent_hour": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxf"
        "YnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpwMQpWX2lzX3NvZnRfYWRqYWNlbnRfaG91cgpwMgp0"
        "cDMKUnA0Ci4="
    ),
    "_is_exact_selected_hour": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxf"
        "YnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2lzX2V4YWN0X3NlbGVjdGVkX2hvdXIKcDEKdFJw"
        "Mgou"
    ),
    "_control_state_log_value": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRvbGxf"
        "YnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2NvbnRyb2xfc3RhdGVfbG9nX3ZhbHVlCnAxCnRS"
        "cDIKLg=="
    ),
    "_has_disabled_class": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyCl9oYXNfZGlzYWJsZWRfY2xhc3MKcDAKLg=="
    ),
}


@dataclass(frozen=True)
class _State:
    enabled: bool = True
    aria_disabled: str = "false"
    disabled_attribute: bool = False
    classes: tuple[str, ...] = ()
    container_classes: tuple[str, ...] = ()
    slide_classes: tuple[str, ...] = ()
    read_error: bool = False


def _candidate(hour: int, state: _State | None = None) -> SearchHourCandidate:
    return SearchHourCandidate(element=object(), hour=hour, state=state or _State())


def test_search_hour_policy_has_exact_sync_owner_boundary() -> None:
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"), filename=str(OWNER_PATH))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == set(OWNER_SYMBOLS)
    assert imports == {
        ("__future__", 0, "annotations", None),
        ("search_driver", 1, "SearchControlState", None),
        ("search_driver", 1, "SearchHourCandidate", None),
    }
    assert not any(
        isinstance(node, (ast.Import, ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree)
    )
    assert policy.__all__ == OWNER_SYMBOLS
    assert all(getattr(policy, name).__module__ == OWNER_MODULE for name in OWNER_SYMBOLS)


def test_browser_keeps_exact_alias_surface_and_legacy_pickles() -> None:
    for browser_name, owner_name in BROWSER_ALIASES.items():
        assert getattr(browser._PydollSession, browser_name) is getattr(policy, owner_name)
    assert browser._has_disabled_class is policy.has_disabled_class

    assert len({name for name in vars(browser) if not name.startswith("_")}) == 84
    private_names = {
        name for name in vars(browser) if name.startswith("_") and not name.startswith("__")
    }
    assert len(private_names) == 29
    assert "_search_hour_policy_owner" not in private_names
    assert not hasattr(browser, "__all__")

    targets = {
        **{
            browser_name: getattr(policy, owner_name)
            for browser_name, owner_name in BROWSER_ALIASES.items()
        },
        "_has_disabled_class": policy.has_disabled_class,
    }
    for legacy_name, payload in LEGACY_PICKLES.items():
        assert pickle.loads(base64.b64decode(payload)) is targets[legacy_name]


def test_search_hour_policy_has_one_consumer_and_passive_import_orders() -> None:
    canonical_consumers: set[str] = set()
    legacy_reentries: set[str] = set()
    for module_path in sorted(SOURCE_ROOT.rglob("*.py")):
        if module_path == OWNER_PATH:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if (
                    node.level == 1
                    and node.module == "korail_sidecar.pydoll"
                    and any(alias.name == "search_hour_policy" for alias in node.names)
                ):
                    canonical_consumers.add(relative_path)
                if (
                    relative_path != "korail_pydoll_browser.py"
                    and node.module == "korail_pydoll_browser"
                    and any(alias.name == "_has_disabled_class" for alias in node.names)
                ):
                    legacy_reentries.add(relative_path)
            elif (
                relative_path != "korail_pydoll_browser.py"
                and isinstance(node, ast.Attribute)
                and node.attr == "_has_disabled_class"
            ):
                legacy_reentries.add(relative_path)

    assert canonical_consumers == {"korail_pydoll_browser.py"}
    assert legacy_reentries == set()

    script = r"""
import importlib
import json
import sys

modules = {
    "browser": "rail_waitlist.korail_pydoll_browser",
    "owner": "rail_waitlist.korail_sidecar.pydoll.search_hour_policy",
}
importlib.import_module(modules[sys.argv[1]])
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import search_hour_policy as owner

aliases = {
    "_control_state_log_value": "control_state_log_value",
    "_current_hour_window": "current_hour_window",
    "_hour_window_signature": "hour_window_signature",
    "_is_exact_hour_catalog": "is_exact_hour_catalog",
    "_is_exact_selected_hour": "is_exact_selected_hour",
    "_is_soft_adjacent_hour": "is_soft_adjacent_hour",
    "_is_soft_aria_hour": "is_soft_aria_hour",
    "_is_soft_dom_hour": "is_soft_dom_hour",
}
print(json.dumps({
    "identity": all(
        getattr(browser._PydollSession, old) is getattr(owner, new)
        for old, new in aliases.items()
    ) and browser._has_disabled_class is owner.has_disabled_class,
    "optional_backend_loaded": any(
        name == "pydoll" or name.startswith("pydoll.") for name in sys.modules
    ),
}, sort_keys=True))
"""
    for first_import in ("owner", "browser"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, first_import],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "identity": True,
            "optional_backend_loaded": False,
        }


def test_window_and_signature_preserve_exact_control_state() -> None:
    current = [
        _candidate(hour, _State(slide_classes=("slick-slide", "slick-current")))
        for hour in range(5)
    ]
    enabled_tail = _candidate(5, _State(slide_classes=("slick-slide",)))
    disabled_tail = _candidate(6, _State(enabled=False, slide_classes=("slick-slide",)))
    candidates = [*current, enabled_tail, disabled_tail]

    assert policy.current_hour_window(candidates) == [*current, enabled_tail]
    assert policy.current_hour_window([current[0], enabled_tail, current[1]]) == []

    state = _State(
        enabled=False,
        aria_disabled="true",
        disabled_attribute=True,
        classes=("control",),
        container_classes=("current",),
        slide_classes=("slick-slide",),
        read_error=True,
    )
    assert policy.control_state_log_value(state) == (
        False,
        "true",
        True,
        ("control",),
        ("current",),
        ("slick-slide",),
        True,
    )
    assert policy.hour_window_signature([_candidate(9, state)]) == (
        (9, policy.control_state_log_value(state)),
    )


def test_soft_hour_classification_is_exact_and_fail_closed() -> None:
    soft_aria = _candidate(
        7,
        _State(enabled=False, aria_disabled="true", slide_classes=("slick-active",)),
    )
    soft_dom = _candidate(
        8,
        _State(enabled=False, aria_disabled="true", slide_classes=("slick-slide",)),
    )
    disabled = _candidate(
        9,
        _State(
            enabled=False,
            aria_disabled="true",
            container_classes=("disabled",),
            slide_classes=("slick-active", "slick-slide"),
        ),
    )
    detached = _candidate(
        10,
        _State(
            enabled=False,
            aria_disabled="true",
            slide_classes=("slick-active", "slick-slide"),
            read_error=True,
        ),
    )

    assert policy.is_soft_aria_hour(soft_aria) is True
    assert policy.is_soft_dom_hour(soft_dom) is True
    assert (
        policy.is_soft_dom_hour(
            _candidate(
                11,
                _State(
                    enabled=False,
                    aria_disabled="true",
                    slide_classes=("slick-slide", "slick-cloned"),
                ),
            )
        )
        is False
    )
    assert policy.has_disabled_class(("control", "slick-disabled")) is True
    assert policy.has_disabled_class(("control", "active")) is False
    for candidate in (disabled, detached):
        assert policy.is_soft_aria_hour(candidate) is False
        assert policy.is_soft_dom_hour(candidate) is False


def test_catalog_adjacent_and_selected_policies_keep_fail_closed_boundaries() -> None:
    catalog = [_candidate(hour) for hour in range(24)]
    assert policy.is_exact_hour_catalog(catalog) is True
    assert policy.is_exact_hour_catalog([*catalog[:-1], _candidate(22)]) is False

    current = [
        _candidate(hour, _State(slide_classes=("slick-slide", "slick-current")))
        for hour in range(5)
    ]
    adjacent = [
        _candidate(
            hour,
            _State(enabled=False, aria_disabled="true", slide_classes=("slick-active",)),
        )
        for hour in range(5, 10)
    ]
    assert policy.is_soft_adjacent_hour([*current, *adjacent], current, adjacent[2]) is True
    assert policy.is_soft_adjacent_hour([*current, *adjacent], current, current[0]) is False

    target = _candidate(9, _State(enabled=False, aria_disabled="true"))
    peer = _candidate(8)
    assert (
        policy.is_exact_selected_hour(
            [peer, target],
            [target],
            target_date_is_selected=True,
            pre_picker_hour_matches=True,
        )
        is True
    )
    assert (
        policy.is_exact_selected_hour(
            [_candidate(8, _State(enabled=False)), target],
            [target],
            target_date_is_selected=True,
            pre_picker_hour_matches=True,
        )
        is False
    )
    assert (
        policy.is_exact_selected_hour(
            [peer, target],
            [target],
            target_date_is_selected=False,
            pre_picker_hour_matches=True,
        )
        is False
    )
