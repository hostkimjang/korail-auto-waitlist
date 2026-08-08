from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

from rail_waitlist import korail_browser_bridge as legacy
from rail_waitlist.browser_companion import http, snapshot_overlay

API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
LEGACY_MODULE = "rail_waitlist.korail_browser_bridge"
HTTP_MODULE = "rail_waitlist.browser_companion.http"
OVERLAY_MODULE = "rail_waitlist.browser_companion.snapshot_overlay"

PUBLIC_SYMBOLS = {
    "APIRouter",
    "Annotated",
    "AnyHttpUrl",
    "AsyncSession",
    "BridgeChallenge",
    "BridgeClientId",
    "BridgePrincipal",
    "BridgeToken",
    "BrowserCompanionChallenge",
    "BrowserCompanionChallengeCreate",
    "BrowserCompanionChallengeRead",
    "BrowserCompanionCredential",
    "BrowserCompanionCredentialRead",
    "BrowserCompanionPairing",
    "BrowserCompanionPairingCreate",
    "BrowserCompanionPairingExchange",
    "BrowserCompanionPairingRead",
    "BrowserCompanionPairingResult",
    "BrowserCompanionStatus",
    "CHALLENGE_FRESHNESS",
    "Depends",
    "EXTENSION_ORIGIN",
    "FRESHNESS",
    "HTTPException",
    "Header",
    "KORAIL_BROWSER_COMPANION_SOURCE",
    "KOREA",
    "KorailBrowserSeatSnapshot",
    "KorailBrowserSnapshotBatch",
    "KorailBrowserSnapshotCreate",
    "KorailBrowserSnapshotRead",
    "MAX_OUTSTANDING_CHALLENGES",
    "PAIRING_FRESHNESS",
    "Request",
    "Response",
    "SNAPSHOT_BUDGET",
    "SNAPSHOT_BUDGET_WINDOW",
    "SNAPSHOT_PATH",
    "SOURCE",
    "SeatAvailabilityAction",
    "SeatAvailabilityProvenance",
    "SeatClass",
    "SeatClassAvailability",
    "SeatObservationStatus",
    "Session",
    "TimetableItem",
    "UTC",
    "ValidationError",
    "ZoneInfo",
    "admin_router",
    "annotations",
    "auth_rate_limiter",
    "browser_companion_status",
    "create_browser_companion_challenge",
    "create_browser_companion_pairing",
    "create_korail_snapshot",
    "dataclass",
    "datetime",
    "delete",
    "exchange_browser_companion_pairing",
    "func",
    "get_session",
    "get_settings",
    "hashlib",
    "keyed_hash",
    "normalize_official_train_number",
    "overlay_korail_browser_snapshots",
    "re",
    "require_admin",
    "require_bridge_credential",
    "revoke_browser_companion_credential",
    "router",
    "secrets",
    "select",
    "timedelta",
    "update",
    "uuid",
}
PRIVATE_SYMBOLS = {
    "_as_utc",
    "_consume_snapshot_budget",
    "_consume_snapshot_challenge",
    "_extension_origin",
    "_require_bridge_enabled",
    "_seat_actions",
    "_snapshot_key",
}
HTTP_DEFINITIONS = {
    "BridgePrincipal",
    "_consume_snapshot_budget",
    "_consume_snapshot_challenge",
    "_extension_origin",
    "_require_bridge_enabled",
    "browser_companion_status",
    "create_browser_companion_challenge",
    "create_browser_companion_pairing",
    "create_korail_snapshot",
    "exchange_browser_companion_pairing",
    "require_bridge_credential",
    "revoke_browser_companion_credential",
}
OVERLAY_DEFINITIONS = {
    "_as_utc",
    "_seat_actions",
    "_snapshot_key",
    "overlay_korail_browser_snapshots",
}
ANNOTATED_ALIASES = {"BridgeChallenge", "BridgeClientId", "BridgeToken", "Session"}
PRE_MOVE_PICKLES = {
    "BridgePrincipal": (
        "gASVOwAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwP"
        "QnJpZGdlUHJpbmNpcGFslJOULg=="
    ),
    "_require_bridge_enabled": (
        "gASVQwAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwX"
        "X3JlcXVpcmVfYnJpZGdlX2VuYWJsZWSUk5Qu"
    ),
    "_extension_origin": (
        "gASVPQAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwR"
        "X2V4dGVuc2lvbl9vcmlnaW6Uk5Qu"
    ),
    "require_bridge_credential": (
        "gASVRQAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwZ"
        "cmVxdWlyZV9icmlkZ2VfY3JlZGVudGlhbJSTlC4="
    ),
    "browser_companion_status": (
        "gASVRAAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwY"
        "YnJvd3Nlcl9jb21wYW5pb25fc3RhdHVzlJOULg=="
    ),
    "create_browser_companion_pairing": (
        "gASVTAAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwg"
        "Y3JlYXRlX2Jyb3dzZXJfY29tcGFuaW9uX3BhaXJpbmeUk5Qu"
    ),
    "revoke_browser_companion_credential": (
        "gASVTwAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwj"
        "cmV2b2tlX2Jyb3dzZXJfY29tcGFuaW9uX2NyZWRlbnRpYWyUk5Qu"
    ),
    "exchange_browser_companion_pairing": (
        "gASVTgAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwi"
        "ZXhjaGFuZ2VfYnJvd3Nlcl9jb21wYW5pb25fcGFpcmluZ5STlC4="
    ),
    "create_browser_companion_challenge": (
        "gASVTgAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwi"
        "Y3JlYXRlX2Jyb3dzZXJfY29tcGFuaW9uX2NoYWxsZW5nZZSTlC4="
    ),
    "_consume_snapshot_challenge": (
        "gASVRwAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwb"
        "X2NvbnN1bWVfc25hcHNob3RfY2hhbGxlbmdllJOULg=="
    ),
    "_consume_snapshot_budget": (
        "gASVRAAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwY"
        "X2NvbnN1bWVfc25hcHNob3RfYnVkZ2V0lJOULg=="
    ),
    "create_korail_snapshot": (
        "gASVQgAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwW"
        "Y3JlYXRlX2tvcmFpbF9zbmFwc2hvdJSTlC4="
    ),
    "_as_utc": (
        "gASVMwAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwHX2FzX3V0Y5STlC4="
    ),
    "_snapshot_key": (
        "gASVOQAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwN"
        "X3NuYXBzaG90X2tleZSTlC4="
    ),
    "overlay_korail_browser_snapshots": (
        "gASVTAAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwg"
        "b3ZlcmxheV9rb3JhaWxfYnJvd3Nlcl9zbmFwc2hvdHOUk5Qu"
    ),
    "_seat_actions": (
        "gASVOQAAAAAAAACMI3JhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfYnJpZGdllIwN"
        "X3NlYXRfYWN0aW9uc5STlC4="
    ),
}


def _resolved_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    try:
        relative_parent = path.relative_to(SOURCE_ROOT).parent
    except ValueError:
        return node.module or ""
    package = ["rail_waitlist", *relative_parent.parts]
    keep = max(0, len(package) - node.level + 1)
    return ".".join([*package[:keep], *([] if node.module is None else [node.module])])


def _resolved_name(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolved_name(node.value, bindings)
        return f"{parent}.{node.attr}" if parent is not None else None
    return None


def _module_references(source: str, path: Path, module: str) -> list[str]:
    tree = ast.parse(source, filename=str(path))
    bindings: dict[str, str] = {}
    references: list[str] = []
    parent, _, member = module.rpartition(".")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local_name] = alias.name if alias.asname else local_name
                if alias.name == module:
                    references.append(f"{node.lineno}:import")
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from(path, node)
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = ".".join(
                        part for part in (resolved, alias.name) if part
                    )
            if resolved == module or (
                resolved == parent and any(alias.name in {member, "*"} for alias in node.names)
            ):
                references.append(f"{node.lineno}:from")

    for _ in range(2):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _resolved_name(node.value, bindings)
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = value

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _resolved_name(node, bindings) == module:
            references.append(f"{node.lineno}:attribute")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if (
            isinstance(first, ast.Constant)
            and first.value == module
            and _resolved_name(node.func, bindings) in {"__import__", "importlib.import_module"}
        ):
            references.append(f"{node.lineno}:dynamic")
        if (
            _resolved_name(node.func, bindings) == "getattr"
            and len(node.args) >= 2
            and _resolved_name(node.args[0], bindings) == parent
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == member
        ):
            references.append(f"{node.lineno}:getattr")
    return sorted(set(references))


def _tree(relative_path: str) -> tuple[Path, ast.Module]:
    path = SOURCE_ROOT / relative_path
    return path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _definitions(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _type_aliases(tree: ast.Module) -> set[str]:
    return {node.name.id for node in tree.body if isinstance(node, ast.TypeAlias)}


def _imported_symbols(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and _resolved_import_from(path, node) == module
        for alias in node.names
    }


def test_legacy_facade_preserves_the_exact_runtime_surface_without_definitions() -> None:
    _, tree = _tree("korail_browser_bridge.py")
    imported_names = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert _definitions(tree) == set()
    assert _type_aliases(tree) == set()
    assert not any(isinstance(node, (ast.Assign, ast.AnnAssign)) for node in tree.body)
    assert imported_names == PUBLIC_SYMBOLS | PRIVATE_SYMBOLS
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == PRIVATE_SYMBOLS
    assert not hasattr(legacy, "__all__")


def test_bridge_definitions_aliases_and_routers_have_one_canonical_owner() -> None:
    _, http_tree = _tree("browser_companion/http.py")
    _, overlay_tree = _tree("browser_companion/snapshot_overlay.py")

    assert _definitions(http_tree) == HTTP_DEFINITIONS
    assert _definitions(overlay_tree) == OVERLAY_DEFINITIONS
    assert _type_aliases(http_tree) == set()
    assert _type_aliases(overlay_tree) == set()
    assert http.__all__ == ("admin_router", "router")
    assert snapshot_overlay.__all__ == ("overlay_korail_browser_snapshots",)

    for name in HTTP_DEFINITIONS:
        value = getattr(http, name)
        assert value.__module__ == HTTP_MODULE
        assert getattr(legacy, name) is value
    for name in OVERLAY_DEFINITIONS:
        value = getattr(snapshot_overlay, name)
        assert value.__module__ == OVERLAY_MODULE
        assert getattr(legacy, name) is value
    for name in ANNOTATED_ALIASES | {"admin_router", "router"}:
        assert getattr(legacy, name) is getattr(http, name)


def test_all_pre_move_bridge_pickles_restore_to_canonical_identity() -> None:
    assert set(PRE_MOVE_PICKLES) == HTTP_DEFINITIONS | OVERLAY_DEFINITIONS
    for name, payload in PRE_MOVE_PICKLES.items():
        owner = http if name in HTTP_DEFINITIONS else snapshot_overlay
        assert pickle.loads(base64.b64decode(payload)) is getattr(owner, name)


def test_canonical_legacy_main_and_timetable_import_orders_preserve_identity() -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "http": "rail_waitlist.browser_companion.http",
    "overlay": "rail_waitlist.browser_companion.snapshot_overlay",
    "legacy": "rail_waitlist.korail_browser_bridge",
    "main": "rail_waitlist.main",
    "timetable": "rail_waitlist.timetable_management.application",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_browser_bridge" in sys.modules
from rail_waitlist import korail_browser_bridge as legacy
from rail_waitlist import main
from rail_waitlist.browser_companion import http, snapshot_overlay
from rail_waitlist.timetable_management import application

http_definitions = %r
overlay_definitions = %r
print(json.dumps({
    "legacy_loaded_before": legacy_loaded_before,
    "legacy_surface": [
        len({name for name in vars(legacy) if not name.startswith("_")}),
        len({
            name for name in vars(legacy)
            if name.startswith("_") and not name.startswith("__")
        }),
        hasattr(legacy, "__all__"),
    ],
    "local_identity": all(
        getattr(legacy, name) is getattr(http, name) for name in http_definitions
    ) and all(
        getattr(legacy, name) is getattr(snapshot_overlay, name)
        for name in overlay_definitions
    ),
    "main_identity": all((
        main.browser_bridge_router is http.router,
        main.browser_companion_admin_router is http.admin_router,
    )),
    "timetable_identity": (
        application.overlay_korail_browser_snapshots
        is snapshot_overlay.overlay_korail_browser_snapshots
    ),
}, sort_keys=True))
""" % (sorted(HTTP_DEFINITIONS), sorted(OVERLAY_DEFINITIONS))

    for first_import in ("http", "overlay", "legacy", "main", "timetable"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, first_import],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "legacy_loaded_before": first_import == "legacy",
            "legacy_surface": [77, 7, False],
            "local_identity": True,
            "main_identity": True,
            "timetable_identity": True,
        }


def test_bridge_reference_scanner_covers_supported_import_forms() -> None:
    path = SOURCE_ROOT / "probe.py"
    examples = (
        ("import rail_waitlist.korail_browser_bridge as old", LEGACY_MODULE),
        ("from rail_waitlist.korail_browser_bridge import router", LEGACY_MODULE),
        ("from rail_waitlist import korail_browser_bridge as old", LEGACY_MODULE),
        ("from .korail_browser_bridge import router", LEGACY_MODULE),
        ("import rail_waitlist as rw; rw.korail_browser_bridge.router", LEGACY_MODULE),
        (f"import importlib; importlib.import_module('{LEGACY_MODULE}')", LEGACY_MODULE),
        (f"from importlib import import_module as load; load('{LEGACY_MODULE}')", LEGACY_MODULE),
        (f"__import__('{LEGACY_MODULE}')", LEGACY_MODULE),
        ("from rail_waitlist.browser_companion.http import router", HTTP_MODULE),
        ("from rail_waitlist.browser_companion import http", HTTP_MODULE),
        ("from .browser_companion.http import router", HTTP_MODULE),
        (f"import importlib; importlib.import_module('{HTTP_MODULE}')", HTTP_MODULE),
        (
            "from rail_waitlist.browser_companion.snapshot_overlay import "
            "overlay_korail_browser_snapshots",
            OVERLAY_MODULE,
        ),
        ("from rail_waitlist.browser_companion import snapshot_overlay", OVERLAY_MODULE),
        (f"__import__('{OVERLAY_MODULE}')", OVERLAY_MODULE),
    )
    for source, module in examples:
        assert _module_references(source, path, module), source


def test_production_consumers_use_the_two_canonical_owners_without_legacy_reentry() -> None:
    http_consumers: set[str] = set()
    overlay_consumers: set[str] = set()
    legacy_consumers: set[str] = set()
    facade_path = SOURCE_ROOT / "korail_browser_bridge.py"
    http_path = SOURCE_ROOT / "browser_companion" / "http.py"
    overlay_path = SOURCE_ROOT / "browser_companion" / "snapshot_overlay.py"

    for root in (SOURCE_ROOT, API_ROOT / "scripts", REPOSITORY_ROOT / "scripts"):
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
            source = path.read_text(encoding="utf-8")
            if path not in {facade_path, http_path} and _module_references(
                source, path, HTTP_MODULE
            ):
                http_consumers.add(relative_path)
            if path not in {facade_path, overlay_path} and _module_references(
                source, path, OVERLAY_MODULE
            ):
                overlay_consumers.add(relative_path)
            if path != facade_path and _module_references(source, path, LEGACY_MODULE):
                legacy_consumers.add(relative_path)

    assert http_consumers == {"apps/api/src/rail_waitlist/main.py"}
    assert overlay_consumers == {
        "apps/api/src/rail_waitlist/browser_companion/http.py",
        "apps/api/src/rail_waitlist/timetable_management/application.py",
    }
    assert legacy_consumers == set()
    assert _imported_symbols(SOURCE_ROOT / "main.py", HTTP_MODULE) == {
        "admin_router",
        "router",
    }
    assert _imported_symbols(
        SOURCE_ROOT / "timetable_management" / "application.py", OVERLAY_MODULE
    ) == {"overlay_korail_browser_snapshots"}
    assert _imported_symbols(http_path, OVERLAY_MODULE) == {"SOURCE", "_as_utc"}


def test_owner_dependencies_are_exact_and_never_reverse_into_the_facade() -> None:
    http_path, http_tree = _tree("browser_companion/http.py")
    overlay_path, overlay_tree = _tree("browser_companion/snapshot_overlay.py")

    def feature_dependencies(path: Path, tree: ast.Module) -> set[str]:
        return {
            _resolved_import_from(path, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and _resolved_import_from(path, node).startswith("rail_waitlist")
        }

    assert feature_dependencies(http_path, http_tree) == {
        "rail_waitlist.auth",
        "rail_waitlist.browser_companion.models",
        "rail_waitlist.browser_companion.schemas",
        "rail_waitlist.browser_companion.snapshot_overlay",
        "rail_waitlist.config",
        "rail_waitlist.database",
        "rail_waitlist.domain",
    }
    assert feature_dependencies(overlay_path, overlay_tree) == {
        "rail_waitlist.browser_companion.models",
        "rail_waitlist.browser_companion.schemas",
        "rail_waitlist.official_rail_identity",
        "rail_waitlist.timetable_management.schemas",
    }
    for path in (http_path, overlay_path):
        source = path.read_text(encoding="utf-8")
        assert _module_references(source, path, LEGACY_MODULE) == []
        assert _module_references(source, path, "rail_waitlist.main") == []
        assert (
            _module_references(source, path, "rail_waitlist.timetable_management.application") == []
        )
    overlay_source = overlay_path.read_text(encoding="utf-8")
    assert _module_references(overlay_source, overlay_path, HTTP_MODULE) == []


def test_snapshot_overlay_is_a_read_only_query_boundary() -> None:
    path, tree = _tree("browser_companion/snapshot_overlay.py")
    sqlalchemy_symbols = _imported_symbols(path, "sqlalchemy")
    mutating_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"add", "add_all", "commit", "delete", "flush", "rollback", "update"}
    }

    assert sqlalchemy_symbols == {"select"}
    assert mutating_calls == set()
    assert not any(isinstance(node, ast.Delete) for node in ast.walk(tree))
