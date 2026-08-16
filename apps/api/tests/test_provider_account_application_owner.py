from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

from rail_waitlist import provider_accounts as legacy
from rail_waitlist.provider_account_management import application as owner
from rail_waitlist.provider_account_management import contracts

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "rail_waitlist"
OWNER_PATH = PACKAGE_ROOT / "provider_account_management" / "application.py"
FACADE_PATH = PACKAGE_ROOT / "provider_accounts.py"
OWNER_MODULE = "rail_waitlist.provider_account_management.application"
LEGACY_MODULE = "rail_waitlist.provider_accounts"

LOCAL_DEFINITIONS = {
    "ProviderAccountGenerationConflict": ast.ClassDef,
    "_require_supported_provider": ast.FunctionDef,
    "_mask_login_id": ast.FunctionDef,
    "_infer_legacy_login_method": ast.FunctionDef,
    "_decrypt_credentials": ast.FunctionDef,
    "get_enabled_provider_credentials": ast.AsyncFunctionDef,
    "has_authenticated_provider_account": ast.AsyncFunctionDef,
    "provider_account_read": ast.FunctionDef,
    "unconfigured_provider_account_read": ast.FunctionDef,
    "list_provider_accounts": ast.AsyncFunctionDef,
    "get_next_provider_credential_version": ast.AsyncFunctionDef,
    "upsert_provider_account": ast.AsyncFunctionDef,
    "delete_provider_account": ast.AsyncFunctionDef,
    "update_provider_auth_status": ast.AsyncFunctionDef,
}
OWNER_ASSIGNMENTS = {
    "ProviderCredentials",
    "RailLoginMethod",
    "SUPPORTED_ACCOUNT_PROVIDERS",
    "PROVIDER_AUTH_STATUSES",
}
OWNER_IMPORTS = {
    ("__future__", 0, "annotations", None),
    ("dataclasses", 0, "dataclass", "dataclass"),
    ("dataclasses", 0, "field", "field"),
    ("datetime", 0, "UTC", None),
    ("datetime", 0, "datetime", None),
    ("sqlalchemy", 0, "select", None),
    ("sqlalchemy.exc", 0, "IntegrityError", None),
    ("sqlalchemy.ext.asyncio", 0, "AsyncSession", None),
    ("domain", 2, "Provider", None),
    ("security", 2, "secret_box", None),
    (None, 1, "contracts", "_account_contracts"),
    ("models", 1, "RailProviderAccount", None),
    ("schemas", 1, "RailProviderAccountRead", None),
    ("schemas", 1, "RailProviderAccountUpsert", None),
    ("schemas", 1, "RailProviderAuthStatus", None),
    (
        "auth_recovery_runtime",
        1,
        "resume_watches_after_verified_provider_login",
        None,
    ),
}
OWNER_DIRECT_IMPORTS = {("typing", "_typing")}
LEGACY_PUBLIC = {
    "annotations",
    "AsyncSession",
    "IntegrityError",
    "PROVIDER_AUTH_STATUSES",
    "Provider",
    "ProviderAccountGenerationConflict",
    "ProviderCredentials",
    "RailLoginMethod",
    "RailProviderAccount",
    "RailProviderAccountRead",
    "RailProviderAccountUpsert",
    "RailProviderAuthStatus",
    "SUPPORTED_ACCOUNT_PROVIDERS",
    "UTC",
    "dataclass",
    "delete_provider_account",
    "datetime",
    "field",
    "get_enabled_provider_credentials",
    "get_next_provider_credential_version",
    "has_authenticated_provider_account",
    "list_provider_accounts",
    "provider_account_read",
    "secret_box",
    "select",
    "unconfigured_provider_account_read",
    "update_provider_auth_status",
    "upsert_provider_account",
}
LEGACY_PRIVATE = {
    "_account_contracts",
    "_decrypt_credentials",
    "_infer_legacy_login_method",
    "_mask_login_id",
    "_require_supported_provider",
}
PRE_MOVE_PICKLES = {
    "ProviderAccountGenerationConflict": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMK"
        "UHJvdmlkZXJBY2NvdW50R2VuZXJhdGlvbkNvbmZsaWN0CnAwCi4="
    ),
    "_require_supported_provider": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKX3JlcXVpcmVfc3VwcG9ydGVkX3Byb3ZpZGVyCnAwCi4="
    ),
    "_mask_login_id": ("Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKX21hc2tfbG9naW5faWQKcDAKLg=="),
    "_infer_legacy_login_method": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKX2luZmVyX2xlZ2FjeV9sb2dpbl9tZXRob2QKcDAKLg=="
    ),
    "_decrypt_credentials": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKX2RlY3J5cHRfY3JlZGVudGlhbHMKcDAKLg=="
    ),
    "get_enabled_provider_credentials": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMK"
        "Z2V0X2VuYWJsZWRfcHJvdmlkZXJfY3JlZGVudGlhbHMKcDAKLg=="
    ),
    "has_authenticated_provider_account": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMK"
        "aGFzX2F1dGhlbnRpY2F0ZWRfcHJvdmlkZXJfYWNjb3VudApwMAou"
    ),
    "provider_account_read": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKcHJvdmlkZXJfYWNjb3VudF9yZWFkCnAwCi4="
    ),
    "unconfigured_provider_account_read": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMK"
        "dW5jb25maWd1cmVkX3Byb3ZpZGVyX2FjY291bnRfcmVhZApwMAou"
    ),
    "list_provider_accounts": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKbGlzdF9wcm92aWRlcl9hY2NvdW50cwpwMAou"
    ),
    "get_next_provider_credential_version": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMK"
        "Z2V0X25leHRfcHJvdmlkZXJfY3JlZGVudGlhbF92ZXJzaW9uCnAwCi4="
    ),
    "upsert_provider_account": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKdXBzZXJ0X3Byb3ZpZGVyX2FjY291bnQKcDAKLg=="
    ),
    "delete_provider_account": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKZGVsZXRlX3Byb3ZpZGVyX2FjY291bnQKcDAKLg=="
    ),
    "update_provider_auth_status": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHMKdXBkYXRlX3Byb3ZpZGVyX2F1dGhfc3RhdHVzCnAwCi4="
    ),
}
CANONICAL_CONSUMERS = {
    "rail_waitlist/provider_account_management/http.py": {
        "ProviderAccountGenerationConflict",
        "delete_provider_account",
        "get_next_provider_credential_version",
        "list_provider_accounts",
        "upsert_provider_account",
    },
    "rail_waitlist/provider_adapters/execution.py": {
        "get_enabled_provider_credentials",
    },
    "rail_waitlist/provider_account_management/runtime.py": {
        "SUPPORTED_ACCOUNT_PROVIDERS",
        "get_enabled_provider_credentials",
        "update_provider_auth_status",
    },
    "rail_waitlist/watch_management/application.py": {
        "has_authenticated_provider_account",
    },
    "rail_waitlist/reservations/reconciliation_state_runtime.py": {
        "update_provider_auth_status",
    },
    "rail_waitlist/worker.py": {"update_provider_auth_status"},
    "scripts/check_reservation_credential_fencing_postgres.py": {
        "get_next_provider_credential_version",
        "update_provider_auth_status",
        "upsert_provider_account",
    },
}


def _resolved_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    relative_path = path.relative_to(SOURCE_ROOT)
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


def test_provider_account_owner_has_exact_definitions_and_dependency_boundary() -> None:
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"), filename=str(OWNER_PATH))
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
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == LOCAL_DEFINITIONS
    assert assignments == OWNER_ASSIGNMENTS
    assert imports == OWNER_IMPORTS
    direct_imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert direct_imports == OWNER_DIRECT_IMPORTS
    assert not hasattr(owner, "__all__")
    assert not _module_references(
        OWNER_PATH.read_text(encoding="utf-8"),
        OWNER_PATH,
        LEGACY_MODULE,
    )[0]


def test_provider_account_facade_has_exact_surface_and_owner_identity() -> None:
    tree = ast.parse(FACADE_PATH.read_text(encoding="utf-8"), filename=str(FACADE_PATH))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    owner_imports = {
        (alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "provider_account_management.application"
        and node.level == 1
        for alias in node.names
    }

    assert definitions == []
    assert assignments == []
    facade_symbols = (LEGACY_PUBLIC - {"annotations"}) | LEGACY_PRIVATE
    assert owner_imports == {(name, name) for name in facade_symbols}
    assert {name for name in vars(legacy) if not name.startswith("_")} == LEGACY_PUBLIC
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == LEGACY_PRIVATE
    assert not hasattr(legacy, "__all__")
    for name in LOCAL_DEFINITIONS:
        assert getattr(legacy, name) is getattr(owner, name)
        assert getattr(owner, name).__module__ == OWNER_MODULE
        assert getattr(owner, name).__qualname__ == name
    assert legacy.ProviderCredentials is contracts.ProviderCredentials
    assert legacy.RailLoginMethod is contracts.RailLoginMethod
    assert legacy.dataclass is contracts.dataclass
    assert legacy.field is contracts.field


def test_pre_move_provider_account_pickles_restore_exact_owner_objects() -> None:
    assert set(PRE_MOVE_PICKLES) == set(LOCAL_DEFINITIONS)
    for name, payload in PRE_MOVE_PICKLES.items():
        assert pickle.loads(base64.b64decode(payload)) is getattr(owner, name)

    credential_payload = b"crail_waitlist.provider_accounts\nProviderCredentials\np0\n."
    assert pickle.loads(credential_payload) is contracts.ProviderCredentials


def test_provider_account_owner_and_facade_are_import_order_independent() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "owner-first":
    from rail_waitlist.provider_account_management import application as owner
    facade_loaded_first = "rail_waitlist.provider_accounts" in sys.modules
    from rail_waitlist import provider_accounts as legacy
else:
    from rail_waitlist import provider_accounts as legacy
    from rail_waitlist.provider_account_management import application as owner
    facade_loaded_first = True

from rail_waitlist.provider_account_management import contracts

local_names = json.loads(sys.argv[2])
print(json.dumps({
    "credential_identity": legacy.ProviderCredentials is contracts.ProviderCredentials,
    "facade_loaded_first": facade_loaded_first,
    "local_identity": all(getattr(legacy, name) is getattr(owner, name) for name in local_names),
}, sort_keys=True))
"""
    local_names = json.dumps(sorted(LOCAL_DEFINITIONS))

    for import_order in ("owner-first", "legacy-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order, local_names],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "credential_identity": True,
            "facade_loaded_first": import_order == "legacy-first",
            "local_identity": True,
        }


def test_provider_account_owner_has_exact_consumers_and_no_legacy_reentry() -> None:
    probes = [
        "from rail_waitlist.provider_accounts import upsert_provider_account",
        "import rail_waitlist.provider_accounts as legacy; legacy.update_provider_auth_status",
        "from rail_waitlist import provider_accounts as legacy; legacy.list_provider_accounts",
        "import rail_waitlist as rw; rw.provider_accounts.delete_provider_account",
        (
            "import rail_waitlist.provider_accounts as legacy; "
            "alias = legacy; alias.provider_account_read"
        ),
        (
            "import rail_waitlist.provider_accounts as legacy; "
            "getattr(legacy, 'provider_account_read')"
        ),
        "import importlib; importlib.import_module('rail_waitlist.provider_accounts')",
        "from importlib import import_module as load; load('rail_waitlist.provider_accounts')",
        "__import__('rail_waitlist.provider_accounts')",
    ]
    for source in probes:
        assert _module_references(source, PACKAGE_ROOT / "probe.py", LEGACY_MODULE)[0]

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
