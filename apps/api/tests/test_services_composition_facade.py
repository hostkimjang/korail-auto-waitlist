from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter
from pathlib import Path

from rail_waitlist import services as facade

API_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = API_ROOT / "src" / "rail_waitlist"
SERVICES_PATH = PACKAGE_ROOT / "services.py"
SERVICES_MODULE = "rail_waitlist.services"

LOCAL_DEFINITIONS = {
    "_ensure_focused_observation_capacity": ast.AsyncFunctionDef,
    "_experimental_rail_enabled": ast.FunctionDef,
    "create_watch": ast.AsyncFunctionDef,
    "find_watch": ast.AsyncFunctionDef,
    "apply_watch_transition": ast.AsyncFunctionDef,
    "transition_watch": ast.AsyncFunctionDef,
    "resume_watches_after_verified_provider_login": ast.AsyncFunctionDef,
    "get_or_create_provider_circuit": ast.AsyncFunctionDef,
    "record_seat_observation": ast.AsyncFunctionDef,
    "begin_reservation_attempt": ast.AsyncFunctionDef,
    "complete_reservation_attempt": ast.AsyncFunctionDef,
    "apply_reservation_reconciliation": ast.AsyncFunctionDef,
    "update_watch": ast.AsyncFunctionDef,
    "validate_channel_ids": ast.AsyncFunctionDef,
}
TRANSACTION_PRIMITIVES = {
    "begin",
    "begin_nested",
    "commit",
    "delete",
    "execute",
    "flush",
    "refresh",
    "rollback",
    "scalar",
    "with_for_update",
}
EXTERNAL_TRANSPORT_ROOTS = {
    "aiohttp",
    "httpx",
    "playwright",
    "pydoll",
    "requests",
    "selenium",
    "socket",
    "urllib",
    "websockets",
}
SCRIPT_CONSUMERS = {
    "scripts/check_observation_fencing_postgres.py": {
        "apply_watch_transition",
        "get_or_create_provider_circuit",
        "record_seat_observation",
    },
    "scripts/check_reservation_credential_fencing_postgres.py": {
        "add_outbox_event",
        "apply_watch_transition",
        "begin_reservation_attempt",
        "complete_reservation_attempt",
        "get_or_create_provider_circuit",
        "record_reservation_confirmation",
    },
}
HTTP_STATUS_BY_WRAPPER = {
    "_ensure_focused_observation_capacity": Counter({409: 1}),
    "create_watch": Counter({403: 1, 409: 1, 422: 1}),
    "find_watch": Counter({404: 1}),
    "apply_watch_transition": Counter({409: 1}),
    "transition_watch": Counter({404: 1}),
    "complete_reservation_attempt": Counter({409: 1}),
    "apply_reservation_reconciliation": Counter({409: 1}),
    "update_watch": Counter({404: 1, 409: 1, 422: 1}),
    "validate_channel_ids": Counter({422: 1}),
}
IMPORT_SIGNATURE = (82, "29fa108182ad029396ab10758fe4e4f7d9547d2548f9bfbb68b2ebe73c5a66e7")
RUNTIME_SURFACE_SIGNATURES = {
    "public": (92, "724fc33072b9aa08db8d1fb7c387b12b7f288cdc9224ebede31681338ed48688"),
    "private": (3, "cb020d1b0edd793bebab0d36e625a827de27f02d392410bbe2c48d499ea43a76"),
}
CANONICAL_CALLS_BY_WRAPPER = {
    "_ensure_focused_observation_capacity": (
        "rail_waitlist.watch_management.update_application.ensure_focused_observation_capacity",
    ),
    "_experimental_rail_enabled": ("rail_waitlist.config.get_settings",),
    "create_watch": (
        "rail_waitlist.watch_management.create_application.WatchCreateDependencies",
        "rail_waitlist.watch_management.create_application.create_watch",
        "rail_waitlist.watch_management.schemas.RegistrationEvidenceConflictDetail",
        "rail_waitlist.provider_registry.application.get_timetable_provider",
    ),
    "find_watch": ("rail_waitlist.watch_management.lookup_application.find_watch",),
    "apply_watch_transition": (
        "rail_waitlist.watch_management.transition_application.WatchTransitionDependencies",
        "rail_waitlist.watch_management.transition_application.apply_watch_transition",
    ),
    "transition_watch": (
        "rail_waitlist.watch_management.transition_command_application.transition_watch",
        "rail_waitlist.watch_management.transition_command_application."
        "WatchTransitionCommandDependencies",
    ),
    "resume_watches_after_verified_provider_login": (
        "rail_waitlist.provider_account_management.auth_recovery_application."
        "ProviderAuthRecoveryDependencies",
        "rail_waitlist.provider_account_management.auth_recovery_application."
        "resume_watches_after_verified_provider_login",
    ),
    "get_or_create_provider_circuit": (
        "rail_waitlist.provider_circuit.application.get_or_create_provider_circuit",
    ),
    "record_seat_observation": (
        "rail_waitlist.observations.recording_application.ObservationRecordingDependencies",
        "rail_waitlist.observations.recording_application.record_seat_observation",
    ),
    "begin_reservation_attempt": (
        "rail_waitlist.reservations.attempt_claim_application.ReservationAttemptClaimDependencies",
        "rail_waitlist.reservations.attempt_claim_application.begin_reservation_attempt",
    ),
    "complete_reservation_attempt": (
        "rail_waitlist.reservations.attempt_result_application."
        "ReservationAttemptResultDependencies",
        "rail_waitlist.reservations.attempt_result_application.complete_reservation_attempt",
    ),
    "apply_reservation_reconciliation": (
        "rail_waitlist.reservations.reconciliation_state_runtime."
        "reservation_reconciliation_state_dependencies",
        "rail_waitlist.reservations.reconciliation_state_application."
        "apply_reservation_reconciliation",
    ),
    "update_watch": (
        "rail_waitlist.watch_management.update_application.WatchUpdateDependencies",
        "rail_waitlist.watch_management.update_application.update_watch",
    ),
    "validate_channel_ids": (
        "rail_waitlist.watch_management.update_application.validate_channel_ids",
    ),
}


def _resolved_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    relative_path = path.relative_to(API_ROOT / "src")
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


def _service_references(source: str, path: Path) -> tuple[bool, set[str]]:
    tree = ast.parse(source, filename=str(path))
    bindings: dict[str, str] = {}
    referenced = False
    symbols: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local_name] = alias.name if alias.asname else local_name
                referenced = referenced or alias.name == SERVICES_MODULE
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from(path, node)
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name != "*":
                    bindings[local_name] = ".".join(part for part in (resolved, alias.name) if part)
            if resolved == SERVICES_MODULE:
                referenced = True
                symbols.update(alias.name for alias in node.names)
            elif resolved == "rail_waitlist" and any(
                alias.name in {"services", "*"} for alias in node.names
            ):
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
        if resolved == SERVICES_MODULE:
            referenced = True
        elif resolved is not None and resolved.startswith(f"{SERVICES_MODULE}."):
            referenced = True
            symbol = resolved.removeprefix(f"{SERVICES_MODULE}.").split(".", maxsplit=1)[0]
            if symbol != "services":
                symbols.add(symbol)
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if _resolved_name(node.func, bindings) != "getattr":
            continue
        owner = _resolved_name(node.args[0], bindings)
        attribute = node.args[1]
        if owner == SERVICES_MODULE and isinstance(attribute, ast.Constant):
            referenced = True
            if isinstance(attribute.value, str):
                symbols.add(attribute.value)
        elif owner == "rail_waitlist" and isinstance(attribute, ast.Constant):
            referenced = referenced or attribute.value == "services"

    return referenced, symbols


def _function_map(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _import_bindings(tree: ast.AST) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local_name] = alias.name if alias.asname else local_name
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from(SERVICES_PATH, node)
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = f"{resolved}.{alias.name}"
    return bindings


def _json_signature(values: object) -> tuple[int, str]:
    if not isinstance(values, list):
        raise TypeError("signature values must be a list")
    payload = json.dumps(values, separators=(",", ":"))
    return len(values), hashlib.sha256(payload.encode()).hexdigest()


def test_services_facade_has_exact_local_definitions_and_runtime_surface() -> None:
    tree = ast.parse(SERVICES_PATH.read_text(encoding="utf-8"), filename=str(SERVICES_PATH))
    definitions = {
        node.name: type(node)
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]

    assert definitions == LOCAL_DEFINITIONS
    assert assignments == []
    assert not hasattr(facade, "__all__")
    assert len(SERVICES_PATH.read_text(encoding="utf-8").splitlines()) <= 456
    import_rows: list[tuple[object, ...]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_rows.extend(("import", alias.name, alias.asname) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            import_rows.extend(
                ("from", node.module, node.level, alias.name, alias.asname) for alias in node.names
            )
    import_rows.sort(key=repr)
    assert _json_signature(import_rows) == IMPORT_SIGNATURE

    runtime_surfaces = {
        "public": sorted(name for name in vars(facade) if not name.startswith("_")),
        "private": sorted(
            name for name in vars(facade) if name.startswith("_") and not name.startswith("__")
        ),
    }
    assert {
        kind: _json_signature(names) for kind, names in runtime_surfaces.items()
    } == RUNTIME_SURFACE_SIGNATURES
    for name in LOCAL_DEFINITIONS:
        value = getattr(facade, name)
        assert value.__module__ == SERVICES_MODULE
        assert value.__qualname__ == name


def test_services_wrappers_only_compose_canonical_delegates_without_io_policy() -> None:
    tree = ast.parse(SERVICES_PATH.read_text(encoding="utf-8"), filename=str(SERVICES_PATH))
    functions = _function_map(tree)

    module_bindings = _import_bindings(tree)
    for wrapper_name, expected in CANONICAL_CALLS_BY_WRAPPER.items():
        bindings = {**module_bindings, **_import_bindings(functions[wrapper_name])}
        actual = Counter(
            resolved
            for node in ast.walk(functions[wrapper_name])
            if isinstance(node, ast.Call)
            for resolved in [_resolved_name(node.func, bindings)]
            if resolved is not None and resolved.startswith("rail_waitlist.")
        )
        assert actual == Counter(expected)

    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    )

    assert called_attributes.isdisjoint(TRANSACTION_PRIMITIVES)
    assert imported_roots.isdisjoint(EXTERNAL_TRANSPORT_ROOTS)


def test_services_has_only_the_exact_legacy_consumers_without_reverse_dependencies() -> None:
    probes = [
        "from .services import create_watch",
        "from rail_waitlist.services import update_watch",
        "from rail_waitlist.services import *",
        "import rail_waitlist.services",
        "import rail_waitlist.services as legacy; legacy.create_watch",
        "import rail_waitlist; rail_waitlist.services.update_watch",
        "from rail_waitlist import services as legacy; legacy.create_watch",
        "import rail_waitlist.services as legacy; alias = legacy; alias.update_watch",
        "import rail_waitlist.services as legacy; getattr(legacy, 'find_watch')",
        "import importlib; importlib.import_module('rail_waitlist.services')",
        "from importlib import import_module as load; load('rail_waitlist.services')",
        "__import__('rail_waitlist.services')",
    ]
    probe_path = PACKAGE_ROOT / "probe.py"
    for source in probes:
        assert _service_references(source, probe_path)[0]

    production_consumers: set[str] = set()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path == SERVICES_PATH:
            continue
        referenced, _ = _service_references(path.read_text(encoding="utf-8"), path)
        if referenced:
            production_consumers.add(path.relative_to(API_ROOT / "src").as_posix())
    assert production_consumers == set()

    script_consumers: dict[str, set[str]] = {}
    for path in sorted((API_ROOT / "scripts").glob("*.py")):
        referenced, symbols = _service_references(path.read_text(encoding="utf-8"), path)
        if referenced:
            script_consumers[path.relative_to(API_ROOT).as_posix()] = symbols
    assert script_consumers == SCRIPT_CONSUMERS


def test_fastapi_is_restricted_to_existing_exception_translation_boundaries() -> None:
    tree = ast.parse(SERVICES_PATH.read_text(encoding="utf-8"), filename=str(SERVICES_PATH))
    functions = _function_map(tree)
    fastapi_imports = {
        (alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "fastapi"
        for alias in node.names
    }
    observed: dict[str, Counter[int]] = {}

    assert fastapi_imports == {("HTTPException", None)}
    for function_name, function in functions.items():
        parent_by_child = {
            child: parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)
        }
        exception_nodes = {
            child
            for handler in ast.walk(function)
            if isinstance(handler, ast.ExceptHandler)
            for child in ast.walk(handler)
        }
        statuses: Counter[int] = Counter()
        for node in ast.walk(function):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "HTTPException"
            ):
                continue
            assert isinstance(parent_by_child[node], ast.Raise)
            assert node in exception_nodes
            status_node = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "status_code"),
                node.args[0] if node.args else None,
            )
            assert isinstance(status_node, ast.Constant)
            assert isinstance(status_node.value, int)
            statuses[status_node.value] += 1
        if statuses:
            observed[function_name] = statuses

    assert observed == HTTP_STATUS_BY_WRAPPER
