from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


@dataclass(frozen=True)
class BoundaryRule:
    name: str
    matches: Callable[[Path], bool]
    forbidden_import_roots: frozenset[str]


DOMAIN_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "SRT",
        "celery",
        "fastapi",
        "httpx",
        "korail2",
        "pydantic",
        "pydoll",
        "playwright",
        "sqlalchemy",
    }
)
PROVIDER_CONTRACT_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "datetime",
        "domain",
        "reservation_confirmation",
        "schemas",
        "typing",
    }
)
PROVIDER_APPLICATION_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "celery_app",
        "config",
        "database",
        "korail_execution",
        "metrics",
        "provider_accounts",
        "providers",
        "srt_execution",
        "srt_provider_adapter",
        "srt_reservation",
        "worker",
    }
)


def _is_domain_module(relative_path: Path) -> bool:
    return relative_path.name == "domain.py"


def _is_application_module(relative_path: Path) -> bool:
    stem = relative_path.stem
    return (
        "application" in relative_path.parts[:-1]
        or stem == "application"
        or stem.startswith("application_")
        or stem.endswith("_application")
    )


def _is_worker_independent_application(relative_path: Path) -> bool:
    return relative_path.as_posix() in {
        "rail_waitlist/notification_management/delivery.py",
        "rail_waitlist/observations/due_pipeline_application.py",
        "rail_waitlist/observations/group_application.py",
        "rail_waitlist/reservations/execution_application.py",
        "rail_waitlist/reservations/reconciliation_application.py",
        "rail_waitlist/watch_management/expiry_application.py",
    }


def _is_due_pipeline_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/observations/due_pipeline_application.py")


def _is_watch_expiry_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/watch_management/expiry_application.py")


def _is_reservation_execution_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/execution_application.py")


def _is_observation_group_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/observations/group_application.py")


def _is_operational_projection_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/observations/operational_projection_application.py"
    )


def _is_observation_cycle_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/observations/cycle_application.py"


def _is_idempotency_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/idempotency/application.py"


def _is_official_page_confirmations(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/official_page_confirmations.py"


def _is_watch_transition_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/transition_policy.py"


def _is_watch_transition_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/transition_application.py"


def _is_watch_update_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/update_application.py"


def _is_reservation_reconciliation_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/reconciliation_application.py")


def _is_payment_hold_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/payment_hold_application.py")


def _is_reservation_attempt_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/reservations/attempt_policy.py"


def _is_reservation_attempt_claim_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/attempt_claim_application.py")


def _is_watch_transition_notification_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/notification_management/watch_transition_application.py"
    )


def _is_provider_contract(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_contracts.py"


def _is_provider_adapter_module(relative_path: Path) -> bool:
    return relative_path.as_posix().startswith("rail_waitlist/provider_adapters/")


def _is_provider_registry_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_registry/application.py"


def _is_ui_preferences_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/ui_preferences/application.py"


def _is_production_module_outside_provider_facade(relative_path: Path) -> bool:
    return relative_path.as_posix() != "rail_waitlist/providers.py"


BOUNDARY_RULES = (
    BoundaryRule(
        name="domain modules are framework and provider independent",
        matches=_is_domain_module,
        forbidden_import_roots=DOMAIN_FORBIDDEN_IMPORT_ROOTS,
    ),
    BoundaryRule(
        name="application modules are independent from FastAPI",
        matches=_is_application_module,
        forbidden_import_roots=frozenset({"fastapi"}),
    ),
    BoundaryRule(
        name="worker-independent applications do not reverse-depend on worker frameworks",
        matches=_is_worker_independent_application,
        forbidden_import_roots=frozenset({"celery", "fastapi", "worker"}),
    ),
    BoundaryRule(
        name="due pipeline application does not own runtime configuration or metrics",
        matches=_is_due_pipeline_application,
        forbidden_import_roots=PROVIDER_APPLICATION_FORBIDDEN_IMPORT_ROOTS,
    ),
    BoundaryRule(
        name="watch expiry application does not own provider runtime concerns",
        matches=_is_watch_expiry_application,
        forbidden_import_roots=frozenset(
            {"config", "metrics", "provider_execution_lease", "providers"}
        ),
    ),
    BoundaryRule(
        name="reservation execution application receives concrete runtime dependencies",
        matches=_is_reservation_execution_application,
        forbidden_import_roots=frozenset(
            {
                "config",
                "celery_app",
                "database",
                "korail_execution",
                "metrics",
                "observations",
                "provider_accounts",
                "provider_execution_lease",
                "providers",
                "services",
                "srt_reservation",
            }
        ),
    ),
    BoundaryRule(
        name="observation group application receives concrete runtime dependencies",
        matches=_is_observation_group_application,
        forbidden_import_roots=frozenset(
            {
                "celery_app",
                "config",
                "database",
                "korail_execution",
                "metrics",
                "provider_accounts",
                "provider_execution_lease",
                "providers",
                "services",
                "srt_execution",
                "srt_provider_adapter",
                "srt_reservation",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="operational projection application stays a pure normalized-result policy",
        matches=_is_operational_projection_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "fastapi",
                "models",
                "outbox",
                "provider_registry",
                "providers",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="observation cycle application stays inside the persistence unit of work",
        matches=_is_observation_cycle_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "metrics",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "reservations",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="idempotency application owns persistence without transport or runtime dependencies",
        matches=_is_idempotency_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "outbox",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="official confirmation persistence uses the canonical idempotency owner",
        matches=_is_official_page_confirmations,
        forbidden_import_roots=frozenset({"services"}),
    ),
    BoundaryRule(
        name="watch transition policy stays pure and runtime independent",
        matches=_is_watch_transition_policy,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "models",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "schemas",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="watch transition application receives transport and runtime dependencies",
        matches=_is_watch_transition_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="watch update application owns persistence without runtime dependencies",
        matches=_is_watch_update_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="reservation reconciliation application depends on provider roles",
        matches=_is_reservation_reconciliation_application,
        forbidden_import_roots=PROVIDER_APPLICATION_FORBIDDEN_IMPORT_ROOTS,
    ),
    BoundaryRule(
        name="payment hold application stays a persistence-read policy",
        matches=_is_payment_hold_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "fastapi",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "schemas",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="reservation attempt policy stays transport and runtime independent",
        matches=_is_reservation_attempt_policy,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "metrics",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "schemas",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="reservation attempt claim application receives runtime side effects",
        matches=_is_reservation_attempt_claim_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="watch transition notification application stays runtime independent",
        matches=_is_watch_transition_notification_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notifications",
                "provider_adapters",
                "provider_registry",
                "providers",
                "security",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider contracts are independent from runtime integrations",
        matches=_is_provider_contract,
        forbidden_import_roots=frozenset(
            {
                "celery_app",
                "config",
                "database",
                "fastapi",
                "korail_execution",
                "metrics",
                "provider_accounts",
                "provider_execution_lease",
                "providers",
                "services",
                "srt_execution",
                "srt_provider_adapter",
                "srt_reservation",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider adapters do not reverse-depend on the compatibility facade",
        matches=_is_provider_adapter_module,
        forbidden_import_roots=frozenset({"provider_registry", "providers"}),
    ),
    BoundaryRule(
        name="provider registry application does not reverse-depend on runtime consumers",
        matches=_is_provider_registry_application,
        forbidden_import_roots=frozenset(
            {"celery_app", "fastapi", "providers", "services", "worker"}
        ),
    ),
    BoundaryRule(
        name="UI preferences application does not reverse-depend on HTTP or legacy services",
        matches=_is_ui_preferences_application,
        forbidden_import_roots=frozenset({"fastapi", "services"}),
    ),
    BoundaryRule(
        name="production modules use canonical provider owners instead of the facade",
        matches=_is_production_module_outside_provider_facade,
        forbidden_import_roots=frozenset({"providers"}),
    ),
)


def _import_components(module_path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.extend((node.lineno, part) for part in alias.name.split(".") if part)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.extend((node.lineno, part) for part in node.module.split(".") if part)
            for alias in node.names:
                imports.extend((node.lineno, part) for part in alias.name.split(".") if part)
    return imports


@pytest.mark.parametrize(
    "source",
    [
        "import rail_waitlist.worker\n",
        "from rail_waitlist.worker import deliver_outbox\n",
        "from rail_waitlist import worker\n",
        "from .. import worker\n",
        "from ..worker import deliver_outbox\n",
    ],
)
def test_import_components_detect_worker_package_imports(tmp_path: Path, source: str) -> None:
    module_path = tmp_path / "delivery.py"
    module_path.write_text(source, encoding="utf-8")

    assert "worker" in {component for _line, component in _import_components(module_path)}


def test_module_dependency_boundaries() -> None:
    violations: list[str] = []
    python_modules = sorted(SOURCE_ROOT.rglob("*.py"))

    for module_path in python_modules:
        relative_path = module_path.relative_to(SOURCE_ROOT)
        imports = _import_components(module_path)
        for rule in BOUNDARY_RULES:
            if not rule.matches(relative_path):
                continue
            for line_number, import_root in imports:
                if import_root in rule.forbidden_import_roots:
                    violations.append(
                        f"{relative_path.as_posix()}:{line_number}: "
                        f"{import_root} violates '{rule.name}'"
                    )

    assert violations == [], "\n".join(violations)


def test_watch_transition_notification_application_does_not_own_transactions_or_locks() -> None:
    module_path = (
        SOURCE_ROOT
        / "rail_waitlist"
        / "notification_management"
        / "watch_transition_application.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert called_attributes.isdisjoint({"begin", "commit", "rollback", "with_for_update"})


def test_observation_cycle_application_does_not_own_transactions_locks_or_outbox() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "observations" / "cycle_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "add_outbox_event" not in called_names
    assert called_attributes.isdisjoint({"begin", "commit", "rollback", "with_for_update"})


def test_idempotency_application_joins_the_callers_unit_of_work() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "idempotency" / "application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert called_attributes.isdisjoint({"begin", "commit", "rollback", "with_for_update"})


def test_watch_transition_policy_imports_only_pure_domain_dependencies() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "transition_policy.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    import_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_roots.add(node.module.split(".")[0])

    assert import_roots <= {
        "__future__",
        "dataclasses",
        "datetime",
        "domain",
        "enum",
        "typing",
    }
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called_attributes.isdisjoint(
        {"begin", "commit", "now", "rollback", "utcnow", "with_for_update"}
    )


def test_watch_transition_application_joins_the_callers_unit_of_work() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "transition_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert called_attributes.isdisjoint(
        {"begin", "commit", "refresh", "rollback", "with_for_update"}
    )


def test_watch_update_application_owns_only_its_command_transaction() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "update_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "HTTPException" not in called_names
    assert called_attributes.isdisjoint({"begin", "rollback"})
    assert {"commit", "refresh", "with_for_update"} <= called_attributes


def test_reservation_attempt_claim_application_joins_the_callers_unit_of_work() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservations" / "attempt_claim_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "begin_nested" in called_attributes
    assert "flush" in called_attributes
    assert called_attributes.isdisjoint({"begin", "commit", "refresh", "rollback"})


def test_observation_group_imports_only_the_canonical_reservation_attempt_policy() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "observations" / "group_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    reservation_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            reservation_imports.update(
                alias.name for alias in node.names if "reservations" in alias.name.split(".")
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "reservations" in node.module.split(".")
        ):
            reservation_imports.add(node.module)

    assert reservation_imports == {"reservations.attempt_policy"}


def test_provider_contract_imports_are_allowlisted() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    import_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_roots.add(node.module.split(".")[0])

    assert import_roots <= PROVIDER_CONTRACT_ALLOWED_IMPORT_ROOTS
