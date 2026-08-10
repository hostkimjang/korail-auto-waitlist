from __future__ import annotations

import ast
import re
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "rail_waitlist"
WORKER_PATH = PACKAGE_ROOT / "worker.py"
COMPOSE_PATH = API_ROOT.parents[1] / "compose.yml"
WORKER_MODULE = "rail_waitlist.worker"

TOP_LEVEL_DEFINITIONS: dict[str, type[ast.AST]] = {
    "_close_execution_adapter": ast.AsyncFunctionDef,
    "_drain_execution_adapter": ast.AsyncFunctionDef,
    "_run_isolated": ast.AsyncFunctionDef,
    "_watch_expiry_dependencies": ast.FunctionDef,
    "_expire_elapsed_watches": ast.AsyncFunctionDef,
    "_recover_stale_reservation_attempts": ast.AsyncFunctionDef,
    "_recover_stale_reservation_attempts_independently": ast.AsyncFunctionDef,
    "_provider_circuit_is_closed": ast.AsyncFunctionDef,
    "_arm_supported_provider_watches": ast.AsyncFunctionDef,
    "_arm_supported_srt_watches": ast.AsyncFunctionDef,
    "_acquire_execution_lease": ast.AsyncFunctionDef,
    "_update_provider_auth_status_in_reservation_transaction": ast.AsyncFunctionDef,
    "_reservation_execution_dependencies": ast.FunctionDef,
    "_reserve_winner": ast.AsyncFunctionDef,
    "_observation_group_dependencies": ast.FunctionDef,
    "_process_watch_group": ast.AsyncFunctionDef,
    "_due_pipeline_dependencies": ast.FunctionDef,
    "_due_sweep_runtime_dependencies": ast.FunctionDef,
    "_process_due_watches": ast.AsyncFunctionDef,
    "_process_watch_now": ast.AsyncFunctionDef,
    "_reconciliation_state_dependencies": ast.FunctionDef,
    "_apply_reservation_reconciliation": ast.AsyncFunctionDef,
    "_reconciliation_dependencies": ast.FunctionDef,
    "_reconcile_reservation_attempt": ast.AsyncFunctionDef,
    "process_due_watches": ast.FunctionDef,
    "recover_abandoned_reservations": ast.FunctionDef,
    "process_watch_now": ast.FunctionDef,
    "reconcile_reservation_attempt": ast.FunctionDef,
    "deliver_outbox": ast.FunctionDef,
}
NESTED_DEFINITIONS = {
    "_observation_group_dependencies": {
        "lease_is_current": ast.AsyncFunctionDef,
        "record_seat_observation": ast.AsyncFunctionDef,
        "reserve_winner": ast.AsyncFunctionDef,
    }
}
CELERY_TASKS = {
    "deliver_outbox": "rail_waitlist.worker.deliver_outbox",
    "process_due_watches": "rail_waitlist.worker.process_due_watches",
    "recover_abandoned_reservations": "rail_waitlist.worker.recover_stale_reservation_attempts",
    "process_watch_now": "rail_waitlist.worker.process_watch_now",
    "reconcile_reservation_attempt": "rail_waitlist.worker.reconcile_reservation_attempt",
}
DEPENDENCY_WIRING = {
    ("_watch_expiry_dependencies", "WatchExpiryDependencies"): {
        "apply_watch_transition": "apply_watch_transition",
    },
    (
        "_recover_stale_reservation_attempts",
        "StaleReservationAttemptRecoveryDependencies",
    ): {
        "add_outbox_event": "add_outbox_event",
        "apply_watch_transition": "apply_watch_transition",
    },
    ("_arm_supported_provider_watches", "WatchArmingDependencies"): {
        "get_execution_provider": "get_execution_provider",
        "session_factory": "SessionFactory",
    },
    ("_acquire_execution_lease", "ExecutionLeaseAcquisitionDependencies"): {
        "session_factory": "SessionFactory",
    },
    ("_reservation_execution_dependencies", "ReservationExecutionDependencies"): {
        "add_outbox_event": "add_outbox_event",
        "apply_watch_transition": "apply_watch_transition",
        "begin_reservation_attempt": "begin_reservation_attempt",
        "complete_reservation_attempt": "complete_reservation_attempt",
        "get_or_create_provider_circuit": "get_or_create_provider_circuit",
        "provider_call_errors": "(ProviderUnavailable, RuntimeError, ValueError)",
        "record_reservation_confirmation": "record_reservation_confirmation",
        "session_factory": "SessionFactory",
        "srt_exact_reservation_source": "SRT_RESERVATION_SOURCE",
        "update_provider_auth_status": ("_update_provider_auth_status_in_reservation_transaction"),
    },
    ("_observation_group_dependencies", "ObservationGroupDependencies"): {
        "add_outbox_event": "add_outbox_event",
        "apply_watch_transition": "apply_watch_transition",
        "finish_observation_cycle": "finish_observation_cycle",
        "get_or_create_provider_circuit": "get_or_create_provider_circuit",
        "is_confirmed_absent_retry_source": "is_confirmed_absent_retry_source",
        "is_payment_hold_ended": "is_payment_hold_ended",
        "latest_observation_fingerprint": "latest_observation_fingerprint",
        "lease_is_current": "lease_is_current",
        "lease_is_current_in_session": ("cast(LockedLeaseCurrent, lock_execution_lease_current)"),
        "provider_call_errors": "(ProviderUnavailable, RuntimeError, ValueError)",
        "record_seat_observation": "record_seat_observation",
        "reserve_winner": "reserve_winner",
        "session_factory": "SessionFactory",
    },
    ("_observation_group_dependencies", "ObservationRecordingDependencies"): {
        "add_outbox_event": "add_outbox_event",
        "apply_operational_projection": "apply_operational_projection",
        "apply_watch_transition": "apply_watch_transition",
    },
    ("_process_watch_group", "WatchGroupRuntimeDependencies"): {
        "acquire_execution_lease": "_acquire_execution_lease",
        "close_execution_adapter": "_close_execution_adapter",
        "drain_execution_adapter": "_drain_execution_adapter",
        "get_execution_provider": "get_execution_provider",
        "observation_group_dependencies": "_observation_group_dependencies",
        "process_watch_group_observation": "process_watch_group_observation",
        "session_factory": "SessionFactory",
        "watch_group_provider": "watch_group_provider",
    },
    ("_due_pipeline_dependencies", "DuePipelineDependencies"): {
        "arm_provider_watches": "_arm_supported_provider_watches",
        "close_execution_adapter": "_close_execution_adapter",
        "expire_elapsed_watches": "_expire_elapsed_watches",
        "get_execution_provider": "get_execution_provider",
        "process_watch_group": "_process_watch_group",
        "reconcile_reservation_attempt": "_reconcile_reservation_attempt",
        "recover_stale_reservation_attempts": "_recover_stale_reservation_attempts",
        "reservation_reconciliation_due_clause": ("_reservation_reconciliation_due_clause"),
        "session_factory": "SessionFactory",
    },
    ("_due_sweep_runtime_dependencies", "DueSweepRuntimeDependencies"): {
        "due_pipeline_dependencies": "_due_pipeline_dependencies",
        "korail_background_enabled": (
            "lambda: korail_background_monitoring_enabled(get_settings())"
        ),
        "process_due_pipeline": "process_due_pipeline",
        "record_group_count": "WATCH_GROUPS.inc",
        "select_provider_arm_targets": "select_provider_arm_targets_policy",
    },
    (
        "_reconciliation_state_dependencies",
        "reservation_reconciliation_state_dependencies",
    ): {
        "add_outbox_event_override": "add_outbox_event",
        "apply_watch_transition_override": "apply_watch_transition",
        "record_reservation_confirmation_override": "record_reservation_confirmation",
        "utc_instant_override": "_utc_instant",
    },
    ("_reconciliation_dependencies", "ReconciliationDependencies"): {
        "acquire_execution_lease": "_acquire_execution_lease",
        "apply_reconciliation": "_apply_reservation_reconciliation",
        "close_execution_adapter": "_close_execution_adapter",
        "drain_execution_adapter": "_drain_execution_adapter",
        "get_execution_provider": "get_execution_provider",
        "provider_circuit_is_closed": "_provider_circuit_is_closed",
        "session_factory": "SessionFactory",
    },
}
COMPOSE_ENTRYPOINTS = {
    "experimental-rail": [
        "celery",
        "-A",
        "rail_waitlist.worker.celery_app",
        "worker",
        "--loglevel=INFO",
        "--queues=experimental-rail",
        "--concurrency=1",
        "--hostname=experimental-rail@%h",
    ],
    "notification-worker": [
        "celery",
        "-A",
        "rail_waitlist.worker.celery_app",
        "worker",
        "--loglevel=INFO",
        "--queues=notifications",
        "--concurrency=1",
        "--hostname=notifications@%h",
    ],
    "maintenance-worker": [
        "celery",
        "-A",
        "rail_waitlist.worker.celery_app",
        "worker",
        "--loglevel=INFO",
        "--queues=maintenance",
        "--concurrency=1",
        "--hostname=maintenance@%h",
    ],
    "scheduler": [
        "celery",
        "-A",
        "rail_waitlist.worker.celery_app",
        "beat",
        "--loglevel=INFO",
        "--pidfile=/tmp/celerybeat.pid",
        "--schedule=/tmp/celerybeat-schedule",
    ],
    "worker": [
        "celery",
        "-A",
        "rail_waitlist.worker.celery_app",
        "worker",
        "--loglevel=INFO",
        "--queues=rail",
        "--concurrency=1",
        "--hostname=rail@%h",
    ],
}


def _worker_tree() -> ast.Module:
    return ast.parse(WORKER_PATH.read_text(encoding="utf-8"), filename=str(WORKER_PATH))


def _top_level_assignments(tree: ast.Module) -> set[str]:
    assignments: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            assignments.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assignments.add(node.target.id)
    return assignments


def _call_name(call: ast.Call) -> str:
    return ast.unparse(call.func)


def _attribute_root_name(node: ast.Attribute) -> str | None:
    value: ast.expr = node
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else None


def _resolved_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts[:-1])
    keep = max(0, len(package_parts) - node.level + 1)
    imported_parts = [] if node.module is None else node.module.split(".")
    return ".".join([*package_parts[:keep], *imported_parts])


def _imports_module(path: Path, target: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name == target for alias in node.names):
            return True
        if not isinstance(node, ast.ImportFrom):
            continue
        resolved = _resolved_import_from(path, node)
        if resolved == target:
            return True
        if any(f"{resolved}.{alias.name}" == target for alias in node.names):
            return True
    return False


def _compose_commands() -> dict[str, list[str]]:
    service: str | None = None
    commands: dict[str, list[str]] = {}
    for line in COMPOSE_PATH.read_text(encoding="utf-8").splitlines():
        service_match = re.fullmatch(r"  ([a-z0-9-]+):", line)
        if service_match:
            service = service_match.group(1)
            continue
        command_match = re.fullmatch(r"    command: (\[.*\])", line)
        if service is not None and command_match:
            command = ast.literal_eval(command_match.group(1))
            if isinstance(command, list) and all(isinstance(item, str) for item in command):
                commands[service] = command
    return commands


def test_worker_has_exact_composition_root_shape() -> None:
    tree = _worker_tree()
    definitions = {
        node.name: type(node)
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    nested_definitions: dict[str, dict[str, type[ast.AST]]] = {}
    for function in tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nested = {
            node.name: type(node)
            for node in ast.walk(function)
            if node is not function
            and isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if nested:
            nested_definitions[function.name] = nested

    assert definitions == TOP_LEVEL_DEFINITIONS
    assert nested_definitions == NESTED_DEFINITIONS
    assert _top_level_assignments(tree) == {"LOGGER"}


def test_worker_has_exact_dependency_object_wiring() -> None:
    tree = _worker_tree()
    dependency_names = {dependency_name for _, dependency_name in DEPENDENCY_WIRING}
    actual: dict[tuple[str, str], dict[str | None, str]] = {}
    for function in tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            dependency_name = _call_name(call)
            if dependency_name not in dependency_names:
                continue
            key = (function.name, dependency_name)
            assert key not in actual
            assert call.args == []
            actual[key] = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}

    assert actual == DEPENDENCY_WIRING


def test_worker_does_not_reclaim_sql_http_or_provider_transport_policy() -> None:
    tree = _worker_tree()
    import_from = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    direct_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    sqlalchemy_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in import_from
        if (node.module or "").split(".", maxsplit=1)[0] == "sqlalchemy"
        for alias in node.names
    }
    provider_transport_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in import_from
        if (node.module or "").startswith(("provider_adapters", "srt_sidecar"))
        for alias in node.names
    }
    forbidden_import_roots = {
        "aiohttp",
        "fastapi",
        "httpx",
        "playwright",
        "pydoll",
        "requests",
        "secrets",
        "security",
        "urllib",
    }
    forbidden_calls = {
        "begin",
        "commit",
        "delete",
        "execute",
        "flush",
        "insert",
        "rollback",
        "scalar",
        "scalars",
        "select",
        "text",
        "update",
    }
    called_names = {
        _call_name(node).rsplit(".", maxsplit=1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    adapter_calls = {
        _call_name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and _attribute_root_name(node.func) == "adapter"
    }
    sensitive_markers = {
        "authorization",
        "ciphertext",
        "cookie",
        "credential",
        "decrypt",
        "encrypt",
        "password",
        "secret",
        "token",
    }
    allowed_sensitive_attributes = {"expected_credential_version"}
    sensitive_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr not in allowed_sensitive_attributes
        and any(marker in node.attr.lower() for marker in sensitive_markers)
    }

    assert sqlalchemy_imports == {("sqlalchemy.ext.asyncio", 0, "AsyncSession", None)}
    assert provider_transport_imports == {
        (
            "provider_adapters.korail_execution",
            1,
            "korail_background_monitoring_enabled",
            None,
        ),
        ("srt_sidecar.reservation", 1, "SRT_RESERVATION_SOURCE", None),
    }
    assert not (
        {name.split(".", maxsplit=1)[0] for name in direct_imports} & forbidden_import_roots
    )
    assert (
        not {(node.module or "").split(".", maxsplit=1)[0] for node in import_from}
        & forbidden_import_roots
    )
    assert not called_names & forbidden_calls
    assert adapter_calls == set()
    assert sensitive_attributes == set()


def test_worker_has_exact_celery_tasks_and_compose_entrypoints() -> None:
    tree = _worker_tree()
    registered_tasks: dict[str, str] = {}
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call) or _call_name(decorator) != "celery_app.task":
                continue
            task_name = next(
                (
                    keyword.value.value
                    for keyword in decorator.keywords
                    if keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            assert task_name is not None
            registered_tasks[function.name] = task_name

    commands = _compose_commands()
    worker_entrypoints = {
        service: command
        for service, command in commands.items()
        if "rail_waitlist.worker.celery_app" in command
    }
    assert registered_tasks == CELERY_TASKS
    assert worker_entrypoints == COMPOSE_ENTRYPOINTS


def test_production_modules_do_not_reverse_depend_on_worker_root() -> None:
    import_consumers = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*.py")
        if path != WORKER_PATH and _imports_module(path, WORKER_MODULE)
    }

    assert import_consumers == set()
