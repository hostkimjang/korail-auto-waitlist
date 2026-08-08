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
from rail_waitlist import korail_pydoll_reservation_actor as legacy
from rail_waitlist.korail_sidecar.pydoll import reservation_actor as owner

API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
LEGACY_MODULE = "rail_waitlist.korail_pydoll_reservation_actor"
OWNER_MODULE = "rail_waitlist.korail_sidecar.pydoll.reservation_actor"

PUBLIC_SYMBOLS = {
    "AcquireReservationSession",
    "Awaitable",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSourceUnavailable",
    "Callable",
    "DirectSearchUrl",
    "DiscardIfCredentialChanged",
    "DiscardWithState",
    "EnsureAuthenticatedSession",
    "KORAIL_ROUTE_HEADING",
    "KorailCredentialInput",
    "KorailReservationOutcome",
    "KorailReservationRequest",
    "KorailReservationResult",
    "KorailReservationSeatClass",
    "KorailSessionActorState",
    "Protocol",
    "PydollAuthenticationSessionLease",
    "PydollPageSnapshot",
    "PydollReservationActor",
    "PydollReservationSession",
    "ReservationIdentityGuard",
    "ResponseSafetyGuard",
    "UniqueReservationTarget",
    "annotations",
    "assert_reservation_identity",
    "asyncio",
    "clock_time",
    "date",
    "datetime",
    "has_unique_reservation_target",
    "normalize_korail_station",
    "normalize_korail_train_number",
    "re",
    "replace",
    "sys",
}
WILDCARD_SYMBOLS = (
    "KorailReservationOutcome",
    "KorailReservationRequest",
    "KorailReservationResult",
    "KorailReservationSeatClass",
    "PydollReservationActor",
    "PydollReservationSession",
    "assert_reservation_identity",
    "has_unique_reservation_target",
)
OWNER_DEFINITIONS = {
    "AcquireReservationSession",
    "EnsureAuthenticatedSession",
    "PydollReservationActor",
    "PydollReservationSession",
    "assert_reservation_identity",
    "has_unique_reservation_target",
}
OWNER_TYPE_ALIASES = {
    "DirectSearchUrl",
    "DiscardIfCredentialChanged",
    "DiscardWithState",
    "ReservationIdentityGuard",
    "ResponseSafetyGuard",
    "UniqueReservationTarget",
}
LEGACY_PICKLES = {
    "PydollReservationSession": (
        "gASVTgAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "GFB5ZG9sbFJlc2VydmF0aW9uU2Vzc2lvbpSTlC4="
    ),
    "AcquireReservationSession": (
        "gASVTwAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "GUFjcXVpcmVSZXNlcnZhdGlvblNlc3Npb26Uk5Qu"
    ),
    "EnsureAuthenticatedSession": (
        "gASVUAAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "GkVuc3VyZUF1dGhlbnRpY2F0ZWRTZXNzaW9ulJOULg=="
    ),
    "DirectSearchUrl": (
        "gASVRQAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "D0RpcmVjdFNlYXJjaFVybJSTlC4="
    ),
    "DiscardIfCredentialChanged": (
        "gASVUAAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "GkRpc2NhcmRJZkNyZWRlbnRpYWxDaGFuZ2VklJOULg=="
    ),
    "DiscardWithState": (
        "gASVRgAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "EERpc2NhcmRXaXRoU3RhdGWUk5Qu"
    ),
    "ResponseSafetyGuard": (
        "gASVSQAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "E1Jlc3BvbnNlU2FmZXR5R3VhcmSUk5Qu"
    ),
    "ReservationIdentityGuard": (
        "gASVTgAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "GFJlc2VydmF0aW9uSWRlbnRpdHlHdWFyZJSTlC4="
    ),
    "UniqueReservationTarget": (
        "gASVTQAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "F1VuaXF1ZVJlc2VydmF0aW9uVGFyZ2V0lJOULg=="
    ),
    "assert_reservation_identity": (
        "gASVUQAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "G2Fzc2VydF9yZXNlcnZhdGlvbl9pZGVudGl0eZSTlC4="
    ),
    "has_unique_reservation_target": (
        "gASVUwAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "HWhhc191bmlxdWVfcmVzZXJ2YXRpb25fdGFyZ2V0lJOULg=="
    ),
    "PydollReservationActor": (
        "gASVTAAAAAAAAACMLXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9hY3RvcpSM"
        "FlB5ZG9sbFJlc2VydmF0aW9uQWN0b3KUk5Qu"
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


def test_legacy_reservation_actor_is_a_definition_free_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_pydoll_reservation_actor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    type_aliases = {node.name.id for node in tree.body if isinstance(node, ast.TypeAlias)}
    imported_names = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    }
    owner_imports = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "korail_sidecar.pydoll.reservation_actor"
        and node.level == 1
        for alias in node.names
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
    assert imported_names == (PUBLIC_SYMBOLS - {"annotations"}) | {"__all__"}
    assert owner_imports == OWNER_DEFINITIONS | OWNER_TYPE_ALIASES | {"__all__"}
    assert assignments == {}
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == set()
    assert owner.__all__ == WILDCARD_SYMBOLS
    assert legacy.__all__ is owner.__all__
    for symbol in PUBLIC_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(owner, symbol)


def test_reservation_actor_definitions_have_one_canonical_owner() -> None:
    path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "reservation_actor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    type_aliases = {node.name.id for node in tree.body if isinstance(node, ast.TypeAlias)}

    assert definitions == OWNER_DEFINITIONS
    assert type_aliases == OWNER_TYPE_ALIASES
    for symbol in OWNER_DEFINITIONS | OWNER_TYPE_ALIASES:
        value = getattr(owner, symbol)
        assert value.__module__ == owner.__name__
        assert getattr(legacy, symbol) is value

    assert browser.PydollReservationActor is owner.PydollReservationActor
    assert browser.assert_actor_reservation_identity is owner.assert_reservation_identity
    assert browser._snapshot_has_unique_reservation_target is owner.has_unique_reservation_target


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_pre_move_reservation_actor_pickles_restore_to_canonical_identity(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser"])
def test_reservation_actor_import_orders_are_passive_and_avoid_legacy_reentry(
    first_import: str,
) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.reservation_actor",
    "legacy": "rail_waitlist.korail_pydoll_reservation_actor",
    "browser": "rail_waitlist.korail_pydoll_browser",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_reservation_actor" in sys.modules
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_reservation_actor as legacy
from rail_waitlist.korail_sidecar.pydoll import reservation_actor as owner

public_symbols = {name for name in vars(owner) if not name.startswith("_")}
print(json.dumps({
    "backend_loaded": sorted(
        name for name in sys.modules if name == "pydoll" or name.startswith("pydoll.")
    ),
    "browser_identity": all((
        browser.PydollReservationActor is owner.PydollReservationActor,
        browser.assert_actor_reservation_identity is owner.assert_reservation_identity,
        browser._snapshot_has_unique_reservation_target
        is owner.has_unique_reservation_target,
    )),
    "legacy_all": list(legacy.__all__),
    "legacy_identity": all(
        getattr(legacy, symbol) is getattr(owner, symbol) for symbol in public_symbols
    ),
    "legacy_loaded_before": legacy_loaded_before,
    "modules": sorted({getattr(owner, symbol).__module__ for symbol in (
        "PydollReservationSession",
        "AcquireReservationSession",
        "EnsureAuthenticatedSession",
        "DirectSearchUrl",
        "DiscardIfCredentialChanged",
        "DiscardWithState",
        "ResponseSafetyGuard",
        "ReservationIdentityGuard",
        "UniqueReservationTarget",
        "assert_reservation_identity",
        "has_unique_reservation_target",
        "PydollReservationActor",
    )}),
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
        "browser_identity": True,
        "legacy_all": list(WILDCARD_SYMBOLS),
        "legacy_identity": True,
        "legacy_loaded_before": first_import == "legacy",
        "modules": [OWNER_MODULE],
    }


def test_reservation_actor_reference_scanner_covers_supported_import_forms() -> None:
    path = SOURCE_ROOT / "probe.py"
    examples = (
        ("import rail_waitlist.korail_pydoll_reservation_actor as old", LEGACY_MODULE),
        (
            "from rail_waitlist.korail_pydoll_reservation_actor import PydollReservationActor",
            LEGACY_MODULE,
        ),
        ("from rail_waitlist import korail_pydoll_reservation_actor as old", LEGACY_MODULE),
        ("from .korail_pydoll_reservation_actor import PydollReservationActor", LEGACY_MODULE),
        (
            "import rail_waitlist as rw; rw.korail_pydoll_reservation_actor.PydollReservationActor",
            LEGACY_MODULE,
        ),
        (f"import importlib; importlib.import_module('{LEGACY_MODULE}')", LEGACY_MODULE),
        (f"from importlib import import_module as load; load('{LEGACY_MODULE}')", LEGACY_MODULE),
        (f"__import__('{LEGACY_MODULE}')", LEGACY_MODULE),
        (
            "from rail_waitlist.korail_sidecar.pydoll.reservation_actor "
            "import PydollReservationActor",
            OWNER_MODULE,
        ),
        (
            "from rail_waitlist.korail_sidecar.pydoll import reservation_actor as owner",
            OWNER_MODULE,
        ),
        (
            "from .korail_sidecar.pydoll.reservation_actor import PydollReservationActor",
            OWNER_MODULE,
        ),
        (f"import importlib; importlib.import_module('{OWNER_MODULE}')", OWNER_MODULE),
    )
    for source, module in examples:
        assert _module_references(source, path, module), source


def test_reservation_actor_has_one_consumer_and_exact_leaf_boundary() -> None:
    canonical_consumers: set[str] = set()
    legacy_consumers: set[str] = set()
    scan_roots = (SOURCE_ROOT, API_ROOT / "scripts", REPOSITORY_ROOT / "scripts")
    facade_path = SOURCE_ROOT / "korail_pydoll_reservation_actor.py"

    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
            source = path.read_text(encoding="utf-8")
            if path != facade_path and _module_references(source, path, OWNER_MODULE):
                canonical_consumers.add(relative_path)
            if _module_references(source, path, LEGACY_MODULE):
                legacy_consumers.add(relative_path)

    assert canonical_consumers == {"apps/api/src/rail_waitlist/korail_pydoll_browser.py"}
    assert legacy_consumers == set()

    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "reservation_actor.py"
    owner_source = owner_path.read_text(encoding="utf-8")
    owner_tree = ast.parse(owner_source, filename=str(owner_path))
    feature_dependencies = {
        _resolved_import_from(owner_path, node)
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom)
        and _resolved_import_from(owner_path, node).startswith("rail_waitlist")
    }
    assert feature_dependencies == {
        "rail_waitlist.korail_sidecar.browser_contracts",
        "rail_waitlist.korail_sidecar.pydoll.auth_actor",
        "rail_waitlist.korail_sidecar.pydoll.auth_contracts",
        "rail_waitlist.korail_sidecar.pydoll.page_contracts",
        "rail_waitlist.korail_sidecar.pydoll.reservation_contracts",
    }

    reverse_dependencies = {
        "rail_waitlist.korail_pydoll_auth_actor",
        "rail_waitlist.korail_pydoll_browser",
        "rail_waitlist.korail_pydoll_login_driver",
        "rail_waitlist.korail_pydoll_reservation_actor",
        "rail_waitlist.korail_pydoll_search_actor",
        "rail_waitlist.korail_sidecar.pydoll.login_driver",
        "rail_waitlist.korail_sidecar.pydoll.reservation_driver",
        "rail_waitlist.korail_sidecar.pydoll.search_actor",
        "rail_waitlist.korail_sidecar.pydoll.search_driver",
    }
    assert {
        module
        for module in reverse_dependencies
        if _module_references(owner_source, owner_path, module)
    } == set()
