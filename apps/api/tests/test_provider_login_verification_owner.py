from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

from rail_waitlist import provider_login_verification as legacy
from rail_waitlist.provider_account_management import login_verification as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "rail_waitlist"
OWNER_PATH = PACKAGE_ROOT / "provider_account_management" / "login_verification.py"
FACADE_PATH = PACKAGE_ROOT / "provider_login_verification.py"
OWNER_MODULE = "rail_waitlist.provider_account_management.login_verification"
LEGACY_MODULE = "rail_waitlist.provider_login_verification"

LOCAL_DEFINITIONS = {
    "ProviderLoginVerificationOutcome": ast.ClassDef,
    "ProviderLoginVerification": ast.ClassDef,
    "ProviderSessionRuntimeState": ast.ClassDef,
    "ProviderSessionRuntimeSnapshot": ast.ClassDef,
    "KorailLoginVerifier": ast.ClassDef,
    "SrtLoginVerifier": ast.ClassDef,
    "ProviderLoginVerifier": ast.ClassDef,
}
OWNER_PUBLIC = {
    "annotations",
    "dataclass",
    "default_srt_reservation_executor",
    "KorailLoginVerifier",
    "KorailSessionStateResult",
    "Protocol",
    "Provider",
    "ProviderCredentials",
    "ProviderLoginVerification",
    "ProviderLoginVerificationOutcome",
    "ProviderLoginVerifier",
    "ProviderSessionRuntimeSnapshot",
    "ProviderSessionRuntimeState",
    "RequestException",
    "SRTError",
    "SRTLoginError",
    "SRTNetFunnelError",
    "SRTResponseError",
    "SrtLoginVerifier",
    "StrEnum",
    "time",
}
OWNER_PRIVATE = {"_SrtSessionActorSnapshot", "_SrtSessionStatus", "_typing"}
OWNER_IMPORTS_FROM = {
    ("__future__", 0, "annotations", None),
    ("dataclasses", 0, "dataclass", None),
    ("enum", 0, "StrEnum", None),
    ("typing", 0, "Protocol", None),
    ("requests", 0, "RequestException", None),
    ("SRT", 0, "SRTError", None),
    ("SRT", 0, "SRTLoginError", None),
    ("SRT", 0, "SRTResponseError", None),
    ("SRT.errors", 0, "SRTNetFunnelError", None),
    ("domain", 2, "Provider", None),
    ("korail_sidecar.contracts", 2, "KorailSessionStateResult", None),
    ("srt_sidecar.contracts", 2, "SrtSessionStatus", "_SrtSessionStatus"),
    (
        "srt_sidecar.reservation",
        2,
        "default_srt_reservation_executor",
        None,
    ),
    (
        "srt_sidecar.session_contract",
        2,
        "SrtSessionActorSnapshot",
        "_SrtSessionActorSnapshot",
    ),
    ("contracts", 1, "ProviderCredentials", None),
}
OWNER_DIRECT_IMPORTS = {("time", None), ("typing", "_typing")}
PRE_MOVE_PICKLES = {
    "ProviderLoginVerificationOutcome": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfbG9naW5fdmVyaWZpY2F0aW9u"
        "ClByb3ZpZGVyTG9naW5WZXJpZmljYXRpb25PdXRjb21lCnAwCi4="
    ),
    "ProviderLoginVerification": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfbG9naW5fdmVyaWZpY2F0aW9u"
        "ClByb3ZpZGVyTG9naW5WZXJpZmljYXRpb24KcDAKLg=="
    ),
    "ProviderSessionRuntimeState": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfbG9naW5fdmVyaWZpY2F0aW9u"
        "ClByb3ZpZGVyU2Vzc2lvblJ1bnRpbWVTdGF0ZQpwMAou"
    ),
    "ProviderSessionRuntimeSnapshot": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfbG9naW5fdmVyaWZpY2F0aW9u"
        "ClByb3ZpZGVyU2Vzc2lvblJ1bnRpbWVTbmFwc2hvdApwMAou"
    ),
    "KorailLoginVerifier": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfbG9naW5fdmVyaWZpY2F0aW9u"
        "CktvcmFpbExvZ2luVmVyaWZpZXIKcDAKLg=="
    ),
    "SrtLoginVerifier": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfbG9naW5fdmVyaWZpY2F0aW9uClNydExvZ2luVmVyaWZpZXIKcDAKLg=="
    ),
    "ProviderLoginVerifier": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfbG9naW5fdmVyaWZpY2F0aW9u"
        "ClByb3ZpZGVyTG9naW5WZXJpZmllcgpwMAou"
    ),
}
CANONICAL_CONSUMERS = {
    "rail_waitlist/korail_browser_seat_source.py": {
        "ProviderLoginVerification",
        "ProviderLoginVerificationOutcome",
    },
    "rail_waitlist/main.py": {"ProviderLoginVerifier"},
    "rail_waitlist/provider_account_management/http.py": {"ProviderLoginVerificationOutcome"},
    "rail_waitlist/provider_adapters/korail_browser_auth_policy.py": {
        "ProviderLoginVerification",
        "ProviderLoginVerificationOutcome",
    },
    "rail_waitlist/provider_account_management/runtime.py": {
        "ProviderLoginVerificationOutcome",
        "ProviderLoginVerifier",
        "ProviderSessionRuntimeState",
    },
}


def _resolved_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    try:
        relative_path = path.relative_to(SOURCE_ROOT)
    except ValueError:
        return node.module or ""
    package_parts = list(relative_path.with_suffix("").parts[:-1])
    keep = max(0, len(package_parts) - node.level + 1)
    imported_parts = [] if node.module is None else node.module.split(".")
    return ".".join([*package_parts[:keep], *imported_parts])


def _resolved_name(node: ast.AST | None, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolved_name(node.value, bindings)
        return None if parent is None else f"{parent}.{node.attr}"
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


def _module_references(source: str, path: Path, module: str) -> tuple[bool, set[str]]:
    tree = ast.parse(source, filename=str(path))
    bindings: dict[str, str] = {}
    referenced = False
    symbols: set[str] = set()
    parent, _, member = module.rpartition(".")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local_name] = alias.name if alias.asname else local_name
                referenced = referenced or alias.name == module
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from(path, node)
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = ".".join(
                        part for part in (resolved, alias.name) if part
                    )
            if resolved == module:
                referenced = True
                symbols.update(alias.name for alias in node.names)
            elif resolved == parent and any(alias.name in {member, "*"} for alias in node.names):
                referenced = True

    for _ in range(4):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            resolved = _resolved_name(node.value, bindings)
            if resolved is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = resolved

    for node in ast.walk(tree):
        resolved = _resolved_name(node, bindings)
        if resolved == module:
            referenced = True
        elif resolved is not None and resolved.startswith(f"{module}."):
            referenced = True
            symbols.add(resolved.removeprefix(f"{module}.").split(".", maxsplit=1)[0])
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if _resolved_name(node.func, bindings) != "getattr":
            continue
        owner_name = _resolved_name(node.args[0], bindings)
        attribute = node.args[1]
        if owner_name == module and isinstance(attribute, ast.Constant):
            referenced = True
            if isinstance(attribute.value, str):
                symbols.add(attribute.value)

    return referenced, symbols


def _top_level_assignments(tree: ast.Module) -> list[ast.Assign | ast.AnnAssign]:
    return [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]


def _top_level_type_aliases(tree: ast.Module) -> list[ast.AST]:
    return [node for node in tree.body if type(node).__name__ == "TypeAlias"]


def test_login_verification_owner_has_exact_definitions_and_strict_dependencies() -> None:
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"), filename=str(OWNER_PATH))
    definitions = {
        node.name: type(node)
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    direct_imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert definitions == LOCAL_DEFINITIONS
    assert _top_level_assignments(tree) == []
    assert _top_level_type_aliases(tree) == []
    assert imports_from == OWNER_IMPORTS_FROM
    assert direct_imports == OWNER_DIRECT_IMPORTS
    assert {name for name in vars(owner) if not name.startswith("_")} == OWNER_PUBLIC
    assert {
        name for name in vars(owner) if name.startswith("_") and not name.startswith("__")
    } == OWNER_PRIVATE
    assert not hasattr(owner, "__all__")
    assert not _module_references(
        OWNER_PATH.read_text(encoding="utf-8"), OWNER_PATH, LEGACY_MODULE
    )[0]


def test_login_verification_facade_has_exact_surface_and_owner_identity() -> None:
    tree = ast.parse(FACADE_PATH.read_text(encoding="utf-8"), filename=str(FACADE_PATH))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    owner_imports = {
        (alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "provider_account_management.login_verification"
        and node.level == 1
        for alias in node.names
    }

    assert definitions == []
    assert _top_level_assignments(tree) == []
    assert _top_level_type_aliases(tree) == []
    assert owner_imports == {(name, name) for name in OWNER_PUBLIC}
    assert {name for name in vars(legacy) if not name.startswith("_")} == OWNER_PUBLIC
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == set()
    assert not hasattr(legacy, "__all__")
    for name in OWNER_PUBLIC:
        assert getattr(legacy, name) is getattr(owner, name)
    for name in LOCAL_DEFINITIONS:
        assert getattr(owner, name).__module__ == OWNER_MODULE
        assert getattr(owner, name).__qualname__ == name


def test_pre_move_login_verification_pickles_restore_exact_owner_objects() -> None:
    assert set(PRE_MOVE_PICKLES) == set(LOCAL_DEFINITIONS)
    for name, payload in PRE_MOVE_PICKLES.items():
        assert pickle.loads(base64.b64decode(payload)) is getattr(owner, name)


def test_login_verification_owner_and_facade_are_import_order_independent() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "owner-first":
    from rail_waitlist.provider_account_management import login_verification as owner
    facade_loaded_first = "rail_waitlist.provider_login_verification" in sys.modules
    from rail_waitlist import provider_login_verification as legacy
else:
    from rail_waitlist import provider_login_verification as legacy
    from rail_waitlist.provider_account_management import login_verification as owner
    facade_loaded_first = True

names = json.loads(sys.argv[2])
local_names = json.loads(sys.argv[3])
print(json.dumps({
    "facade_loaded_first": facade_loaded_first,
    "identity": all(getattr(legacy, name) is getattr(owner, name) for name in names),
    "local_module": all(getattr(owner, name).__module__ == owner.__name__ for name in local_names),
}, sort_keys=True))
"""
    public_names = json.dumps(sorted(OWNER_PUBLIC))
    local_names = json.dumps(sorted(LOCAL_DEFINITIONS))

    for import_order in ("owner-first", "facade-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order, public_names, local_names],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "facade_loaded_first": import_order == "facade-first",
            "identity": True,
            "local_module": True,
        }


def test_login_verification_scanner_detects_direct_module_and_dynamic_access() -> None:
    probes = [
        f"from {OWNER_MODULE} import ProviderLoginVerifier",
        f"from {OWNER_MODULE} import *",
        f"import {OWNER_MODULE} as canonical; canonical.ProviderLoginVerifier",
        (
            "from rail_waitlist.provider_account_management import login_verification as "
            "canonical; canonical.ProviderLoginVerifier"
        ),
        (
            "import rail_waitlist as rw; "
            "rw.provider_account_management.login_verification.ProviderLoginVerifier"
        ),
        f"import {OWNER_MODULE} as canonical; alias = canonical; alias.ProviderLoginVerifier",
        f"import {OWNER_MODULE} as canonical; getattr(canonical, 'ProviderLoginVerifier')",
        f"import importlib; importlib.import_module('{OWNER_MODULE}')",
        f"from importlib import import_module as load; load('{OWNER_MODULE}')",
        f"__import__('{OWNER_MODULE}')",
    ]
    for source in probes:
        assert _module_references(source, PACKAGE_ROOT / "probe.py", OWNER_MODULE)[0]


def test_login_verification_owner_has_exact_consumers_and_no_legacy_reentry() -> None:
    canonical_consumers: dict[str, set[str]] = {}
    legacy_consumers: set[str] = set()
    roots = [(PACKAGE_ROOT, SOURCE_ROOT), (API_ROOT / "scripts", API_ROOT)]
    for root, relative_root in roots:
        for path in sorted(root.rglob("*.py")):
            if path in {OWNER_PATH, FACADE_PATH}:
                continue
            relative_name = path.relative_to(relative_root).as_posix()
            source = path.read_text(encoding="utf-8")
            canonical_reference, symbols = _module_references(source, path, OWNER_MODULE)
            if canonical_reference:
                canonical_consumers[relative_name] = symbols
            if _module_references(source, path, LEGACY_MODULE)[0]:
                legacy_consumers.add(relative_name)

    assert canonical_consumers == CANONICAL_CONSUMERS
    assert legacy_consumers == set()
