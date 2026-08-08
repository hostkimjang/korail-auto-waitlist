from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

from rail_waitlist import provider_runtime as legacy
from rail_waitlist.provider_account_management import runtime as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "rail_waitlist"
OWNER_PATH = PACKAGE_ROOT / "provider_account_management" / "runtime.py"
FACADE_PATH = PACKAGE_ROOT / "provider_runtime.py"
OWNER_MODULE = "rail_waitlist.provider_account_management.runtime"
LEGACY_MODULE = "rail_waitlist.provider_runtime"

LOCAL_DEFINITIONS = {
    "_EnabledAccountRuntime": ast.ClassDef,
    "ProviderRuntimePrewarmRegistry": ast.ClassDef,
    "_load_enabled_account_runtime": ast.AsyncFunctionDef,
    "_account_status": ast.FunctionDef,
    "_prewarm_account": ast.AsyncFunctionDef,
    "_restore_authenticated_account": ast.AsyncFunctionDef,
    "_restore_locally_reusable_session": ast.AsyncFunctionDef,
    "prewarm_provider_sessions": ast.AsyncFunctionDef,
    "recover_provider_sessions_once": ast.AsyncFunctionDef,
    "maintain_provider_sessions": ast.AsyncFunctionDef,
    "run_provider_session_manager": ast.AsyncFunctionDef,
}
OWNER_ASSIGNMENTS = {
    "LOGGER",
    "PROVIDER_AUTH_RECOVERY_INTERVAL_SECONDS",
    "RECOVERABLE_PROVIDER_AUTH_STATUSES",
    "recover_auth_required_provider_sessions_once",
}
OWNER_IMPORTS_FROM = {
    ("__future__", 0, "annotations", None),
    ("dataclasses", 0, "dataclass", None),
    ("dataclasses", 0, "field", None),
    ("datetime", 0, "UTC", None),
    ("datetime", 0, "datetime", None),
    ("sqlalchemy", 0, "select", None),
    ("sqlalchemy.ext.asyncio", 0, "AsyncSession", None),
    ("sqlalchemy.ext.asyncio", 0, "async_sessionmaker", None),
    ("domain", 2, "Provider", None),
    ("application", 1, "SUPPORTED_ACCOUNT_PROVIDERS", None),
    ("application", 1, "get_enabled_provider_credentials", None),
    ("application", 1, "update_provider_auth_status", None),
    ("auth_recovery_runtime", 1, "resume_watches_after_verified_provider_login", None),
    ("contracts", 1, "ProviderCredentials", None),
    ("login_verification", 1, "ProviderLoginVerificationOutcome", None),
    ("login_verification", 1, "ProviderLoginVerifier", None),
    ("login_verification", 1, "ProviderSessionRuntimeState", None),
    ("models", 1, "RailProviderAccount", None),
    ("schemas", 1, "RailProviderAuthStatus", None),
}
OWNER_DIRECT_IMPORTS = {("asyncio", None), ("logging", None)}
OWNER_PUBLIC = {
    "annotations",
    "AsyncSession",
    "LOGGER",
    "PROVIDER_AUTH_RECOVERY_INTERVAL_SECONDS",
    "Provider",
    "ProviderCredentials",
    "ProviderLoginVerificationOutcome",
    "ProviderLoginVerifier",
    "ProviderRuntimePrewarmRegistry",
    "ProviderSessionRuntimeState",
    "RECOVERABLE_PROVIDER_AUTH_STATUSES",
    "RailProviderAccount",
    "RailProviderAuthStatus",
    "SUPPORTED_ACCOUNT_PROVIDERS",
    "UTC",
    "async_sessionmaker",
    "asyncio",
    "dataclass",
    "datetime",
    "field",
    "get_enabled_provider_credentials",
    "logging",
    "maintain_provider_sessions",
    "prewarm_provider_sessions",
    "recover_auth_required_provider_sessions_once",
    "recover_provider_sessions_once",
    "run_provider_session_manager",
    "select",
    "update_provider_auth_status",
}
OWNER_PRIVATE = {
    "_EnabledAccountRuntime",
    "_account_status",
    "_load_enabled_account_runtime",
    "_prewarm_account",
    "_restore_authenticated_account",
    "_restore_locally_reusable_session",
}
PRE_MOVE_PICKLES = {
    "ProviderRuntimePrewarmRegistry": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpQcm92aWRlclJ1bnRpbWVQcmV3YXJt"
        "UmVnaXN0cnkKcDAKLg=="
    ),
    "_EnabledAccountRuntime": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpfRW5hYmxlZEFjY291bnRSdW50aW1lCnAwCi4="
    ),
    "_account_status": ("Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpfYWNjb3VudF9zdGF0dXMKcDAKLg=="),
    "_load_enabled_account_runtime": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpfbG9hZF9lbmFibGVkX2FjY291bnRfcnVudGltZQpwMAou"
    ),
    "_prewarm_account": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpfcHJld2FybV9hY2NvdW50CnAwCi4="
    ),
    "_restore_authenticated_account": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpfcmVzdG9yZV9hdXRoZW50aWNhdGVk"
        "X2FjY291bnQKcDAKLg=="
    ),
    "_restore_locally_reusable_session": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpfcmVzdG9yZV9sb2NhbGx5X3JldXNh"
        "YmxlX3Nlc3Npb24KcDAKLg=="
    ),
    "maintain_provider_sessions": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQptYWludGFpbl9wcm92aWRlcl9zZXNzaW9ucwpwMAou"
    ),
    "prewarm_provider_sessions": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpwcmV3YXJtX3Byb3ZpZGVyX3Nlc3Npb25zCnAwCi4="
    ),
    "recover_provider_sessions_once": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpyZWNvdmVyX3Byb3ZpZGVyX3Nlc3Np"
        "b25zX29uY2UKcDAKLg=="
    ),
    "run_provider_session_manager": (
        "Y3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfcnVudGltZQpydW5fcHJvdmlkZXJfc2Vzc2lvbl9tYW5hZ2VyCnAwCi4="
    ),
}
CANONICAL_CONSUMERS = {
    "rail_waitlist/main.py": {
        "ProviderRuntimePrewarmRegistry",
        "run_provider_session_manager",
    }
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


def _top_level_assignments(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_runtime_owner_has_exact_definitions_dependencies_and_surface() -> None:
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
    assert _top_level_assignments(tree) == OWNER_ASSIGNMENTS
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


def test_runtime_facade_has_exact_surface_and_owner_identity() -> None:
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
        and node.module == "provider_account_management.runtime"
        and node.level == 1
        for alias in node.names
    }

    assert definitions == []
    assert _top_level_assignments(tree) == set()
    assert owner_imports == {(name, name) for name in OWNER_PUBLIC | OWNER_PRIVATE}
    assert {name for name in vars(legacy) if not name.startswith("_")} == OWNER_PUBLIC
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == OWNER_PRIVATE
    assert not hasattr(legacy, "__all__")
    for name in OWNER_PUBLIC | OWNER_PRIVATE:
        assert getattr(legacy, name) is getattr(owner, name)
    for name in LOCAL_DEFINITIONS:
        assert getattr(owner, name).__module__ == OWNER_MODULE
        assert getattr(owner, name).__qualname__ == name


def test_pre_move_runtime_pickles_restore_exact_owner_objects() -> None:
    assert set(PRE_MOVE_PICKLES) == set(LOCAL_DEFINITIONS)
    for name, payload in PRE_MOVE_PICKLES.items():
        assert pickle.loads(base64.b64decode(payload)) is getattr(owner, name)


def test_runtime_owner_and_facade_are_import_order_independent() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "owner-first":
    from rail_waitlist.provider_account_management import runtime as owner
    facade_loaded_first = "rail_waitlist.provider_runtime" in sys.modules
    from rail_waitlist import provider_runtime as legacy
else:
    from rail_waitlist import provider_runtime as legacy
    from rail_waitlist.provider_account_management import runtime as owner
    facade_loaded_first = True

names = json.loads(sys.argv[2])
local_names = json.loads(sys.argv[3])
print(json.dumps({
    "facade_loaded_first": facade_loaded_first,
    "identity": all(getattr(legacy, name) is getattr(owner, name) for name in names),
    "local_module": all(getattr(owner, name).__module__ == owner.__name__ for name in local_names),
}, sort_keys=True))
"""
    names = json.dumps(sorted(OWNER_PUBLIC | OWNER_PRIVATE))
    local_names = json.dumps(sorted(LOCAL_DEFINITIONS))

    for import_order in ("owner-first", "facade-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order, names, local_names],
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


def test_runtime_behavior_uses_canonical_late_patch(monkeypatch) -> None:
    original_facade_binding = legacy.recover_provider_sessions_once

    async def owner_replacement(*_args, **_kwargs) -> int:
        return 0

    monkeypatch.setattr(owner, "recover_provider_sessions_once", owner_replacement)

    assert legacy.recover_provider_sessions_once is original_facade_binding
    assert (
        owner.maintain_provider_sessions.__globals__["recover_provider_sessions_once"]
        is owner_replacement
    )

    async def facade_replacement(*_args, **_kwargs) -> int:
        return 1

    monkeypatch.setattr(legacy, "recover_provider_sessions_once", facade_replacement)

    assert owner.recover_provider_sessions_once is owner_replacement
    assert (
        owner.maintain_provider_sessions.__globals__["recover_provider_sessions_once"]
        is owner_replacement
    )


def test_runtime_has_exact_canonical_consumer_and_no_legacy_reentry() -> None:
    probes = [
        f"from {OWNER_MODULE} import ProviderRuntimePrewarmRegistry",
        f"import {OWNER_MODULE} as canonical; canonical.run_provider_session_manager",
        (
            "from rail_waitlist.provider_account_management import runtime as canonical; "
            "canonical.prewarm_provider_sessions"
        ),
        f"import {OWNER_MODULE} as canonical; getattr(canonical, 'maintain_provider_sessions')",
        f"import importlib; importlib.import_module('{OWNER_MODULE}')",
    ]
    for source in probes:
        assert _module_references(source, PACKAGE_ROOT / "probe.py", OWNER_MODULE)[0]

    canonical_consumers: dict[str, set[str]] = {}
    legacy_consumers: set[str] = set()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path in {OWNER_PATH, FACADE_PATH}:
            continue
        relative_name = path.relative_to(SOURCE_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        canonical_reference, symbols = _module_references(source, path, OWNER_MODULE)
        if canonical_reference:
            canonical_consumers[relative_name] = symbols
        if _module_references(source, path, LEGACY_MODULE)[0]:
            legacy_consumers.add(relative_name)

    assert canonical_consumers == CANONICAL_CONSUMERS
    assert legacy_consumers == set()
