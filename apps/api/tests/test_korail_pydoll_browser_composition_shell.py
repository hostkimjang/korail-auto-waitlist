from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import reservation_contracts

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
BROWSER_PATH = SOURCE_ROOT / "korail_pydoll_browser.py"
BROWSER_MODULE = "rail_waitlist.korail_pydoll_browser"
LOCAL_DEFINITIONS = {
    "PydollBrowserSession": ast.ClassDef,
    "PydollSessionContext": ast.ClassDef,
    "_default_pydoll_session_factory": ast.FunctionDef,
    "PydollKorailBrowserClient": ast.ClassDef,
    "_PydollSession": ast.ClassDef,
    "_PydollSessionContext": ast.ClassDef,
    "_normalize_train_number": ast.FunctionDef,
    "_has_exact_text_marker": ast.FunctionDef,
    "_has_exact_route_markers": ast.FunctionDef,
}
MODULE_ASSIGNMENTS = {
    "_MAX_MORE_RESULT_ACTIONS",
    "_HTTP_REPLAY_ROUTE_CACHE_SIZE",
    "_PROTECTION_SURFACE_SELECTOR",
    "_KORAIL_RESERVATION_LIST_URL",
    "logger",
    "_PydollSessionLease",
    "_credential_fingerprint",
    "KorailCredentialInput",
    "KorailLoginMethod",
    "KorailSessionActorSnapshot",
    "KorailSessionActorState",
    "KorailReservationOutcome",
    "KorailReservationProgressCallback",
    "KorailReservationRequest",
    "KorailReservationResult",
    "KorailReservationSeatClass",
    "_snapshot_has_unique_reservation_target",
    "_has_disabled_class",
    "_ControlState",
    "_sanitized_class_tokens",
    "PydollSessionFactory",
}
METHOD_INVENTORIES = {
    "PydollBrowserSession": (
        "open",
        "navigate",
        "navigate_fresh",
        "choose_station",
        "choose_schedule",
        "current_station",
        "current_schedule",
        "current_passenger",
        "ensure_authenticated",
        "probe_authenticated_session",
        "begin_http_replay_capture",
        "export_http_replay_plan",
        "submit_once",
        "wait_for_result",
        "expand_results",
        "reserve_once",
        "confirmation_correlation_seats_from_fresh_state",
        "read_reservation_list",
        "read_issued_ticket_list",
        "_snapshot",
        "_probe_official_authenticated_session",
        "_has_authenticated_header",
    ),
    "PydollSessionContext": ("__aenter__", "__aexit__"),
    "PydollKorailBrowserClient": (
        "__init__",
        "_session_lock",
        "_active_session",
        "_active_session",
        "_session_actor_state",
        "_session_actor_state",
        "_session_actor_generation",
        "_session_actor_generation",
        "_session_actor_created_at",
        "_session_actor_created_at",
        "_session_actor_last_verified_at",
        "_session_actor_last_verified_at",
        "_session_actor_last_used_at",
        "_session_actor_last_used_at",
        "_validate_page_url",
        "search",
        "reserve_once",
        "_direct_search_url",
        "verify_credentials",
        "read_reservation_detail",
        "prewarm_credentials",
        "session_snapshot",
        "close",
        "_active_http_replays",
        "_active_search_session",
        "_acquire_session",
        "_ensure_authenticated_session",
        "_discard_active_session",
        "_session_reuse_enabled",
        "_assert_reservation_identity",
        "_assert_response_allowed",
        "_read_result",
    ),
    "_PydollSession": (
        "__init__",
        "_browser",
        "_browser",
        "_tab",
        "_tab",
        "_network_callback_id",
        "_network_callback_id",
        "_network_events_enabled_by_session",
        "_network_events_enabled_by_session",
        "_login_go_to",
        "_login_execute_script",
        "_search_query",
        "_search_execute_script",
        "_mark_search_submitted",
        "_reset_login_search_state",
        "__aenter__",
        "__aexit__",
        "open",
        "navigate",
        "navigate_fresh",
        "read_reservation_list",
        "read_issued_ticket_list",
        "_replace_tab",
        "_attach_network_listener",
        "_cleanup_tab_listener",
        "choose_station",
        "choose_schedule",
        "current_station",
        "current_schedule",
        "current_passenger",
        "ensure_authenticated",
        "probe_authenticated_session",
        "_authenticate_in_place",
        "_submit_login_form",
        "_wait_for_login_authentication",
        "_confirm_authenticated_search",
        "_probe_official_authenticated_session",
        "_has_authenticated_header",
        "_wait_for_authenticated_header",
        "_login_step",
        "_wait_for_unique_login_method_tab",
        "_wait_for_login_controls",
        "begin_http_replay_capture",
        "export_http_replay_plan",
        "submit_once",
        "wait_for_result",
        "expand_results",
        "reserve_once",
        "confirmation_correlation_seats_from_fresh_state",
        "_has_exact_preserved_booking_state",
        "_actionable_seat_controls",
        "_seat_price_box_metadata",
        "_row_matches_reservation",
        "_probe_reservation_terminal",
        "_wait_for_result_growth",
        "_snapshot",
        "_issued_ticket_snapshot",
        "_evaluate_value",
        "_evaluate_text",
        "_wait_for_value",
        "_click_exact_text",
        "_wait_for_exact_text",
        "_wait_for_enabled_exact_text",
        "_read_hour_candidates",
        "_wait_for_hour_window_change",
        "_log_hour_window_navigation_failure",
        "_wait_for_hour_animation",
        "_hour_carousel_control_metadata",
        "_find_hour_navigation_control",
        "_swipe_hour_carousel",
        "_navigate_hour_carousel_by_keyboard",
        "_dispatch_mouse_event",
        "_wait_for_schedule",
        "_wait_for_schedule_date",
        "_click_hour_and_confirm",
        "_wait_for_visible_elements",
        "_wait_for_dialog",
        "_find_exact_visible",
        "_has_exact_visible",
        "_visible_elements",
        "_timeout_seconds",
        "_close",
        "_on_response_received",
    ),
    "_PydollSessionContext": ("__init__", "__aenter__", "__aexit__"),
}
IMPLEMENTATION_ISLANDS = {
    "PydollKorailBrowserClient": {
        "__init__",
        "_validate_page_url",
        "read_reservation_detail",
        "close",
    },
    "_PydollSession": {
        "__init__",
        "open",
        "navigate",
        "navigate_fresh",
        "read_reservation_list",
        "read_issued_ticket_list",
        "export_http_replay_plan",
        "_on_response_received",
    },
    "_PydollSessionContext": set(),
}
STATIC_OWNER_HOOKS = {
    "_dom_interaction_owner": {
        "click_exact_text",
        "evaluate_text",
        "evaluate_value",
        "find_exact_visible",
        "has_exact_visible",
        "wait_for_dialog",
        "wait_for_enabled_exact_text",
        "wait_for_exact_text",
        "wait_for_value",
        "wait_for_visible_elements",
    },
    "_live_dom_owner": {"read_control_state", "visible_elements"},
    "_search_hour_carousel_input_owner": {
        "dispatch_mouse_event",
        "navigate_hour_carousel_by_keyboard",
        "swipe_hour_carousel",
    },
    "_search_hour_carousel_observation_owner": {
        "find_hour_navigation_control",
        "hour_carousel_control_metadata",
        "log_hour_window_navigation_failure",
        "read_hour_candidates",
        "wait_for_hour_animation",
        "wait_for_hour_window_change",
    },
    "_search_hour_policy_owner": {
        "control_state_log_value",
        "current_hour_window",
        "hour_window_signature",
        "is_exact_hour_catalog",
        "is_exact_selected_hour",
        "is_soft_adjacent_hour",
        "is_soft_aria_hour",
        "is_soft_dom_hour",
    },
    "_search_schedule_commit_owner": {
        "click_hour_and_confirm",
        "wait_for_schedule",
        "wait_for_schedule_date",
    },
}
CORE_SYMBOLS = (
    "PydollBrowserSession",
    "PydollSessionContext",
    "PydollKorailBrowserClient",
    "_PydollSession",
    "_PydollSessionContext",
    "_default_pydoll_session_factory",
)
CORE_PICKLES = {
    "PydollBrowserSession": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyClB5ZG9sbEJyb3dzZXJTZXNzaW9uCnAwCi4="
    ),
    "PydollSessionContext": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyClB5ZG9sbFNlc3Npb25Db250ZXh0CnAwCi4="
    ),
    "PydollKorailBrowserClient": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyClB5ZG9sbEtvcmFpbEJy"
        "b3dzZXJDbGllbnQKcDAKLg=="
    ),
    "_PydollSession": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyCl9QeWRvbGxTZXNzaW9uCnAwCi4="
    ),
    "_PydollSessionContext": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyCl9QeWRvbGxTZXNzaW9uQ29udGV4dApwMAou"
    ),
    "_default_pydoll_session_factory": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyCl9kZWZhdWx0X3B5ZG9s"
        "bF9zZXNzaW9uX2ZhY3RvcnkKcDAKLg=="
    ),
}


def _resolved_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    relative_parent = path.relative_to(SOURCE_ROOT).parent
    package = ["rail_waitlist", *relative_parent.parts]
    keep = max(0, len(package) - node.level + 1)
    return ".".join([*package[:keep], *([] if node.module is None else [node.module])])


def _resolved_name(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolved_name(node.value, bindings)
        return f"{parent}.{node.attr}" if parent is not None else None
    if isinstance(node, ast.Subscript):
        return _resolved_name(node.value, bindings)
    if isinstance(node, ast.Call) and node.args:
        function = _resolved_name(node.func, bindings)
        first = node.args[0]
        if (
            function in {"__import__", "importlib.import_module"}
            and isinstance(first, ast.Constant)
            and isinstance(first.value, str)
        ):
            return first.value
    return None


def _module_references(source: str, path: Path, module: str) -> bool:
    tree = ast.parse(source, filename=str(path))
    bindings: dict[str, str] = {}
    parent, _, member = module.rpartition(".")
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local_name] = alias.name if alias.asname else local_name
                found = found or alias.name == module
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from(path, node)
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = ".".join(
                        part for part in (resolved, alias.name) if part
                    )
            found = (
                found
                or resolved == module
                or (resolved == parent and any(alias.name in {member, "*"} for alias in node.names))
            )
    for _ in range(3):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _resolved_name(node.value, bindings)
            if value is not None:
                for target in targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = value
    return found or any(
        _resolved_name(node, bindings) == module
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Call))
    )


def _statement_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    module = ast.Module(body=body, type_ignores=[])
    return sum(isinstance(item, ast.stmt) for item in ast.walk(module))


def test_browser_shell_has_exact_local_definitions_assignments_and_surface() -> None:
    source = BROWSER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(BROWSER_PATH))
    definitions = {
        node.name: type(node)
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }

    assert definitions == LOCAL_DEFINITIONS
    assert assignments == MODULE_ASSIGNMENTS
    assert len(source.splitlines()) <= 1_465
    assert len({name for name in vars(browser) if not name.startswith("_")}) == 84
    private_names = {
        name for name in vars(browser) if name.startswith("_") and not name.startswith("__")
    }
    assert len(private_names) == 30
    assert browser._ActorKorailReservedSeat is reservation_contracts.KorailReservedSeat
    assert not hasattr(browser, "__all__")


def test_browser_shell_has_exact_method_and_composition_islands() -> None:
    tree = ast.parse(BROWSER_PATH.read_text(encoding="utf-8"), filename=str(BROWSER_PATH))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    for class_name, expected in METHOD_INVENTORIES.items():
        methods = tuple(
            node.name
            for node in classes[class_name].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        assert methods == expected

    for class_name, expected in IMPLEMENTATION_ISLANDS.items():
        islands = {
            node.name
            for node in classes[class_name].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _statement_count(node) > 5
        }
        assert islands == expected
        for method in (
            node
            for node in classes[class_name].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            nested = [
                node
                for node in ast.walk(method)
                if node is not method
                and isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            assert nested == []

    client_init = next(
        node
        for node in classes["PydollKorailBrowserClient"].body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    client_calls = {
        name
        for node in ast.walk(client_init)
        if isinstance(node, ast.Call)
        for name in [_resolved_name(node.func, {})]
        if name is not None
    }
    client_collaborators = {name for name in client_calls if name.endswith("Actor")}
    assert client_collaborators == {
        "PydollAuthenticationSessionActor",
        "PydollReadOnlySearchActor",
        "PydollReservationActor",
    }

    session = classes["_PydollSession"]
    session_init = next(
        node
        for node in session.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    session_calls = {
        name
        for node in ast.walk(session_init)
        if isinstance(node, ast.Call)
        for name in [_resolved_name(node.func, {})]
        if name is not None
    }
    session_collaborators = {
        name for name in session_calls if name.endswith("DomDriver") or name.endswith("Lifecycle")
    }
    assert session_collaborators == {
        "PydollChromiumLifecycle",
        "PydollLoginDomDriver",
        "PydollReservationDomDriver",
        "PydollSearchDomDriver",
    }

    hooks: dict[str, set[str]] = {}
    for node in session.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "staticmethod"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Attribute)
            and isinstance(value.args[0].value, ast.Name)
        ):
            continue
        hooks.setdefault(value.args[0].value.id, set()).add(value.args[0].attr)
    assert hooks == STATIC_OWNER_HOOKS


def test_browser_shell_has_exact_consumers_and_no_owner_reverse_imports() -> None:
    probe = SOURCE_ROOT / "_browser_shell_reference_probe.py"
    core_targets = tuple(f"{BROWSER_MODULE}.{name}" for name in CORE_SYMBOLS)
    forms = (
        f"from {BROWSER_MODULE} import PydollKorailBrowserClient",
        "from rail_waitlist import korail_pydoll_browser as browser\nbrowser._PydollSession",
        f"import importlib\nbrowser = importlib.import_module('{BROWSER_MODULE}')\n"
        "browser._PydollSessionContext",
    )
    assert all(
        any(_module_references(source, probe, target) for target in core_targets)
        for source in forms
    )

    consumers: dict[str, set[str]] = {}
    for module_path in sorted(SOURCE_ROOT.rglob("*.py")):
        if module_path == BROWSER_PATH:
            continue
        used = {
            target.rpartition(".")[2]
            for target in core_targets
            if _module_references(module_path.read_text(encoding="utf-8"), module_path, target)
        }
        if used:
            consumers[module_path.relative_to(SOURCE_ROOT).as_posix()] = used
    assert consumers == {
        "korail_browser_mode_smoke.py": {
            "PydollKorailBrowserClient",
            "_PydollSession",
            "_PydollSessionContext",
        },
        "korail_sidecar/runtime.py": {"PydollKorailBrowserClient"},
    }

    owner_paths = (
        SOURCE_ROOT / "korail_sidecar" / "pydoll" / "dom_interaction.py",
        SOURCE_ROOT / "korail_sidecar" / "pydoll" / "live_dom.py",
        SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_hour_carousel_input.py",
        SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_hour_carousel_observation.py",
        SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_hour_policy.py",
        SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_schedule_commit.py",
    )
    assert all(
        not _module_references(path.read_text(encoding="utf-8"), path, BROWSER_MODULE)
        for path in owner_paths
    )


def test_browser_shell_core_paths_keep_pickles_and_passive_import_order() -> None:
    for name in CORE_SYMBOLS:
        value = getattr(browser, name)
        assert value.__module__ == BROWSER_MODULE
        assert value.__qualname__ == name
        assert pickle.loads(base64.b64decode(CORE_PICKLES[name])) is value

    script = r"""
import importlib
import json
import sys

browser_module = "rail_waitlist.korail_pydoll_browser"
owners = (
    "rail_waitlist.korail_sidecar.pydoll.dom_interaction",
    "rail_waitlist.korail_sidecar.pydoll.live_dom",
    "rail_waitlist.korail_sidecar.pydoll.search_hour_carousel_input",
    "rail_waitlist.korail_sidecar.pydoll.search_hour_carousel_observation",
    "rail_waitlist.korail_sidecar.pydoll.search_hour_policy",
    "rail_waitlist.korail_sidecar.pydoll.search_schedule_commit",
)
if sys.argv[1] == "browser":
    importlib.import_module(browser_module)
    for module in owners:
        importlib.import_module(module)
else:
    for module in owners:
        importlib.import_module(module)
    importlib.import_module(browser_module)
browser = importlib.import_module(browser_module)
core = (
    "PydollBrowserSession",
    "PydollSessionContext",
    "PydollKorailBrowserClient",
    "_PydollSession",
    "_PydollSessionContext",
    "_default_pydoll_session_factory",
)
print(json.dumps({
    "identity": all(getattr(browser, name).__module__ == browser_module for name in core),
    "optional_backend_loaded": any(
        name == "pydoll" or name.startswith("pydoll.") for name in sys.modules
    ),
    "legacy_facades_loaded": any(
        name in sys.modules
        for name in (
            "rail_waitlist.korail_pydoll_auth_actor",
            "rail_waitlist.korail_pydoll_login_driver",
            "rail_waitlist.korail_pydoll_reservation_actor",
            "rail_waitlist.korail_pydoll_reservation_driver",
            "rail_waitlist.korail_pydoll_search_actor",
            "rail_waitlist.korail_pydoll_search_driver",
        )
    ),
}, sort_keys=True))
"""
    for first_import in ("browser", "owners"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, first_import],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "identity": True,
            "legacy_facades_loaded": False,
            "optional_backend_loaded": False,
        }
