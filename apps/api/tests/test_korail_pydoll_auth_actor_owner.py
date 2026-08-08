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

from rail_waitlist import korail_pydoll_auth_actor as legacy
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import auth_actor as owner
from rail_waitlist.korail_sidecar.pydoll import reservation_actor

API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
LEGACY_MODULE = "rail_waitlist.korail_pydoll_auth_actor"
OWNER_MODULE = "rail_waitlist.korail_sidecar.pydoll.auth_actor"

PUBLIC_SYMBOLS = {
    "ActivePydollAuthenticationSession",
    "Awaitable",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSourceUnavailable",
    "Callable",
    "ContractKorailCredentialInput",
    "ContractKorailLoginMethod",
    "CredentialFingerprint",
    "KorailCredentialInput",
    "KorailLoginMethod",
    "KorailSessionActorSnapshot",
    "KorailSessionActorState",
    "OwnedCleanup",
    "Protocol",
    "PydollAuthenticationSession",
    "PydollAuthenticationSessionActor",
    "PydollAuthenticationSessionContext",
    "PydollAuthenticationSessionFactory",
    "PydollAuthenticationSessionLease",
    "PydollPageSnapshot",
    "ResponseSafetyGuard",
    "StrEnum",
    "annotations",
    "asyncio",
    "credential_fingerprint",
    "dataclass",
    "field",
    "hashlib",
    "sys",
}
OWNER_DEFINITIONS = {
    "ActivePydollAuthenticationSession",
    "KorailSessionActorSnapshot",
    "KorailSessionActorState",
    "PydollAuthenticationSession",
    "PydollAuthenticationSessionActor",
    "PydollAuthenticationSessionContext",
    "PydollAuthenticationSessionFactory",
    "PydollAuthenticationSessionLease",
    "credential_fingerprint",
}
OWNER_TYPE_ALIASES = {"PydollAuthenticationSessionFactory"}
RUNTIME_ALIASES = {
    "CredentialFingerprint",
    "OwnedCleanup",
    "ResponseSafetyGuard",
}
LEGACY_DEFINITION_PICKLES = {
    "KorailSessionActorState": (
        "gASVRgAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwX"
        "S29yYWlsU2Vzc2lvbkFjdG9yU3RhdGWUk5Qu"
    ),
    "KorailSessionActorSnapshot": (
        "gASVSQAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwa"
        "S29yYWlsU2Vzc2lvbkFjdG9yU25hcHNob3SUk5Qu"
    ),
    "credential_fingerprint": (
        "gASVRQAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwW"
        "Y3JlZGVudGlhbF9maW5nZXJwcmludJSTlC4="
    ),
    "PydollAuthenticationSession": (
        "gASVSgAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwb"
        "UHlkb2xsQXV0aGVudGljYXRpb25TZXNzaW9ulJOULg=="
    ),
    "PydollAuthenticationSessionContext": (
        "gASVUQAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwi"
        "UHlkb2xsQXV0aGVudGljYXRpb25TZXNzaW9uQ29udGV4dJSTlC4="
    ),
    "PydollAuthenticationSessionFactory": (
        "gASVUQAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwi"
        "UHlkb2xsQXV0aGVudGljYXRpb25TZXNzaW9uRmFjdG9yeZSTlC4="
    ),
    "ActivePydollAuthenticationSession": (
        "gASVUAAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwh"
        "QWN0aXZlUHlkb2xsQXV0aGVudGljYXRpb25TZXNzaW9ulJOULg=="
    ),
    "PydollAuthenticationSessionLease": (
        "gASVTwAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwg"
        "UHlkb2xsQXV0aGVudGljYXRpb25TZXNzaW9uTGVhc2WUk5Qu"
    ),
    "PydollAuthenticationSessionActor": (
        "gASVTwAAAAAAAACMJnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2FjdG9ylIwg"
        "UHlkb2xsQXV0aGVudGljYXRpb25TZXNzaW9uQWN0b3KUk5Qu"
    ),
}
LEGACY_ALIAS_PICKLES = {
    "ResponseSafetyGuard": (
        "gASVpgAAAAAAAACMD2NvbGxlY3Rpb25zLmFiY5SMFV9DYWxsYWJsZUdlbmVyaWNBbGlhc5ST"
        "lGgAjAhDYWxsYWJsZZSTlF2UKIwycmFpbF93YWl0bGlzdC5rb3JhaWxfc2lkZWNhci5weWRv"
        "bGwucGFnZV9jb250cmFjdHOUjBJQeWRvbGxQYWdlU25hcHNob3SUk5SMCGJ1aWx0aW5zlIwD"
        "c3RylJOUZU6GlIaUUpQu"
    ),
    "OwnedCleanup": (
        "gASVlQAAAAAAAACMD2NvbGxlY3Rpb25zLmFiY5SMFV9DYWxsYWJsZUdlbmVyaWNBbGlhc5ST"
        "lGgAjAhDYWxsYWJsZZSTlF2UjAV0eXBlc5SMDEdlbmVyaWNBbGlhc5STlGgAjAlBd2FpdGFi"
        "bGWUk5SMCGJ1aWx0aW5zlIwGb2JqZWN0lJOUhZSGlFKUYWgIaApOhZSGlFKUhpSGlFKULg=="
    ),
    "CredentialFingerprint": (
        "gASVqQAAAAAAAACMD2NvbGxlY3Rpb25zLmFiY5SMFV9DYWxsYWJsZUdlbmVyaWNBbGlhc5ST"
        "lGgAjAhDYWxsYWJsZZSTlF2UjDJyYWlsX3dhaXRsaXN0LmtvcmFpbF9zaWRlY2FyLnB5ZG9s"
        "bC5hdXRoX2NvbnRyYWN0c5SMFUtvcmFpbENyZWRlbnRpYWxJbnB1dJSTlGGMCGJ1aWx0aW5z"
        "lIwFYnl0ZXOUk5SGlIaUUpQu"
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


def test_legacy_auth_actor_is_a_definition_free_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_pydoll_auth_actor.py"
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
        ("korail_sidecar.pydoll", 1, "auth_actor", "_owner"),
        ("korail_sidecar.pydoll.auth_actor", 1, "Callable", "Callable"),
    }
    assert set(assignments) == PUBLIC_SYMBOLS - {"Callable"}
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == set()
    assert set(owner.__all__) == PUBLIC_SYMBOLS
    assert not hasattr(legacy, "__all__")
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol
        assert getattr(legacy, symbol) is getattr(owner, symbol)
    assert legacy.Callable is owner.Callable


def test_auth_actor_definitions_and_runtime_aliases_have_one_owner() -> None:
    path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "auth_actor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert {node.name.id for node in tree.body if isinstance(node, ast.TypeAlias)} == (
        OWNER_TYPE_ALIASES
    )
    for symbol in OWNER_DEFINITIONS:
        value = getattr(owner, symbol)
        assert value.__module__ == owner.__name__
        assert getattr(legacy, symbol) is value
    for symbol in RUNTIME_ALIASES:
        value = getattr(owner, symbol)
        assert getattr(legacy, symbol) is value
        assert get_origin(value) is owner.Callable
        assert not isinstance(value, TypeAliasType)

    assert browser._ActivePydollSession is owner.ActivePydollAuthenticationSession
    assert browser.AuthKorailSessionActorSnapshot is owner.KorailSessionActorSnapshot
    assert browser.AuthKorailSessionActorState is owner.KorailSessionActorState
    assert browser.PydollAuthenticationSessionActor is owner.PydollAuthenticationSessionActor
    assert browser.PydollAuthenticationSessionLease is owner.PydollAuthenticationSessionLease
    assert browser.credential_fingerprint is owner.credential_fingerprint
    assert reservation_actor.KorailSessionActorState is owner.KorailSessionActorState
    assert (
        reservation_actor.PydollAuthenticationSessionLease is owner.PydollAuthenticationSessionLease
    )


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_DEFINITION_PICKLES.items())
def test_pre_move_auth_actor_definition_pickles_restore_by_identity(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_ALIAS_PICKLES.items())
def test_pre_move_auth_actor_alias_pickles_restore_by_type_and_equality(
    symbol: str,
    payload: str,
) -> None:
    restored = pickle.loads(base64.b64decode(payload))
    current = getattr(owner, symbol)
    assert type(restored) is type(current)
    assert restored == current


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser", "reservation"])
def test_auth_actor_import_orders_are_passive_and_avoid_legacy_reentry(
    first_import: str,
) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.auth_actor",
    "legacy": "rail_waitlist.korail_pydoll_auth_actor",
    "browser": "rail_waitlist.korail_pydoll_browser",
    "reservation": "rail_waitlist.korail_sidecar.pydoll.reservation_actor",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_auth_actor" in sys.modules
from rail_waitlist import korail_pydoll_auth_actor as legacy
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import auth_actor as owner
from rail_waitlist.korail_sidecar.pydoll import reservation_actor as reservation

print(json.dumps({
    "backend_loaded": sorted(
        name for name in sys.modules if name == "pydoll" or name.startswith("pydoll.")
    ),
    "browser_identity": all((
        browser._ActivePydollSession is owner.ActivePydollAuthenticationSession,
        browser.AuthKorailSessionActorSnapshot is owner.KorailSessionActorSnapshot,
        browser.AuthKorailSessionActorState is owner.KorailSessionActorState,
        browser.PydollAuthenticationSessionActor is owner.PydollAuthenticationSessionActor,
        browser.PydollAuthenticationSessionLease is owner.PydollAuthenticationSessionLease,
        browser.credential_fingerprint is owner.credential_fingerprint,
    )),
    "legacy_identity": all(
        getattr(legacy, symbol) is getattr(owner, symbol)
        for symbol in owner.__all__
    ),
    "legacy_loaded_before": legacy_loaded_before,
    "modules": sorted({getattr(owner, symbol).__module__ for symbol in (
        "KorailSessionActorState",
        "KorailSessionActorSnapshot",
        "credential_fingerprint",
        "PydollAuthenticationSession",
        "PydollAuthenticationSessionContext",
        "PydollAuthenticationSessionFactory",
        "ActivePydollAuthenticationSession",
        "PydollAuthenticationSessionLease",
        "PydollAuthenticationSessionActor",
    )}),
    "reservation_identity": all((
        reservation.KorailSessionActorState is owner.KorailSessionActorState,
        reservation.PydollAuthenticationSessionLease
        is owner.PydollAuthenticationSessionLease,
    )),
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
        "legacy_identity": True,
        "legacy_loaded_before": first_import == "legacy",
        "modules": [OWNER_MODULE],
        "reservation_identity": True,
    }


def test_auth_actor_reference_scanner_covers_all_supported_import_forms() -> None:
    path = SOURCE_ROOT / "probe.py"
    examples = (
        "import rail_waitlist.korail_pydoll_auth_actor as old",
        "from rail_waitlist.korail_pydoll_auth_actor import KorailSessionActorState",
        "from rail_waitlist import korail_pydoll_auth_actor as old",
        "from .korail_pydoll_auth_actor import KorailSessionActorState",
        "import rail_waitlist as rw; rw.korail_pydoll_auth_actor.KorailSessionActorState",
        f"import importlib; importlib.import_module('{LEGACY_MODULE}')",
        f"from importlib import import_module as load; load('{LEGACY_MODULE}')",
        f"__import__('{LEGACY_MODULE}')",
    )
    for source in examples:
        assert _module_references(source, path, LEGACY_MODULE), source


def test_auth_actor_has_two_canonical_consumers_and_no_legacy_or_reverse_reentry() -> None:
    canonical_consumers: set[str] = set()
    legacy_consumers: set[str] = set()
    scan_roots = (SOURCE_ROOT, API_ROOT / "scripts", REPOSITORY_ROOT / "scripts")
    compatibility_facades = {
        SOURCE_ROOT / "korail_pydoll_auth_actor.py",
        SOURCE_ROOT / "korail_pydoll_reservation_actor.py",
    }

    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
            source = path.read_text(encoding="utf-8")
            if path not in compatibility_facades and _module_references(source, path, OWNER_MODULE):
                canonical_consumers.add(relative_path)
            if _module_references(source, path, LEGACY_MODULE):
                legacy_consumers.add(relative_path)

    assert canonical_consumers == {
        "apps/api/src/rail_waitlist/korail_pydoll_browser.py",
        "apps/api/src/rail_waitlist/korail_sidecar/pydoll/reservation_actor.py",
    }
    assert legacy_consumers == set()

    owner_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "auth_actor.py"
    owner_source = owner_path.read_text(encoding="utf-8")
    reverse_dependencies = {
        "rail_waitlist.korail_pydoll_browser",
        "rail_waitlist.korail_pydoll_login_driver",
        "rail_waitlist.korail_pydoll_reservation_actor",
        "rail_waitlist.korail_sidecar.pydoll.reservation_actor",
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
