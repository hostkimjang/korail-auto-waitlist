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
        "observations",
        "provider_registry",
        "reservations",
        "schemas",
        "timetable_management",
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
        "rail_waitlist/observations/due_provider_policy.py",
        "rail_waitlist/observations/group_application.py",
        "rail_waitlist/observations/group_runtime.py",
        "rail_waitlist/provider_execution/lease_application.py",
        "rail_waitlist/reservations/execution_application.py",
        "rail_waitlist/reservations/execution_runtime.py",
        "rail_waitlist/reservations/reconciliation_application.py",
        "rail_waitlist/watch_management/expiry_application.py",
    }


def _is_due_pipeline_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/observations/due_pipeline_application.py")


def _is_due_provider_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/observations/due_provider_policy.py"


def _is_watch_expiry_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/watch_management/expiry_application.py")


def _is_reservation_execution_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/execution_application.py")


def _is_reservation_execution_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/reservations/execution_runtime.py"


def _is_korail_provider_confirmation_owner(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/reservations/provider_confirmation/korail.py"
    )


def _is_observation_group_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/observations/group_application.py")


def _is_observation_group_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/observations/group_runtime.py"


def _is_observation_due_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/observations/due_runtime.py"


def _is_operational_projection_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/observations/operational_projection_application.py"
    )


def _is_observation_cycle_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/observations/cycle_application.py"


def _is_observation_recording_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/observations/recording_application.py"


def _is_observation_status_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/observations/status_policy.py"


def _is_idempotency_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/idempotency/application.py"


def _is_idempotency_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/idempotency/models.py"


def _is_event_stream_schema(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/event_stream/schemas.py"


def _is_browser_companion_schema(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/browser_companion/schemas.py"


def _is_browser_companion_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/browser_companion/models.py"


def _is_provider_schema_base(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_schema_base.py"


def _is_official_rail_identity(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/official_rail_identity.py"


def _is_official_page_confirmation_compatibility(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/official_page_confirmations.py"


def _is_official_page_confirmation_schema(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/official_page_confirmation/schemas.py"


def _is_official_page_confirmation_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/official_page_confirmation/models.py"


def _is_official_page_confirmation_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/official_page_confirmation/application.py"


def _is_watch_transition_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/transition_policy.py"


def _is_watch_transition_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/transition_application.py"


def _is_watch_transition_command_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/watch_management/transition_command_application.py"
    )


def _is_watch_transition_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/transition_runtime.py"


def _is_watch_command_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/command_runtime.py"


def _is_watch_create_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/create_application.py"


def _is_watch_lookup_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/lookup_application.py"


def _is_watch_management_schema(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/schemas.py"


def _is_watch_arming_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/arming_application.py"


def _is_provider_auth_recovery_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/provider_account_management/auth_recovery_application.py"
    )


def _is_provider_auth_recovery_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/provider_account_management/auth_recovery_runtime.py"
    )


def _is_admin_auth_schema(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/admin_auth/schemas.py"


def _is_admin_auth_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/admin_auth/models.py"


def _is_provider_circuit_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_circuit/application.py"


def _is_provider_circuit_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_circuit/models.py"


def _is_notification_management_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/notification_management/models.py"


def _is_provider_account_management_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_account_management/models.py"


def _is_provider_account_reservation_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/provider_account_management/reservation_runtime.py"
    )


def _is_provider_execution_contract(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_execution/contracts.py"


def _is_provider_execution_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_execution/models.py"


def _is_provider_execution_lifecycle_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_execution/lifecycle_runtime.py"


def _is_timetable_management_model(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/timetable_management/models.py"


def _is_timetable_management_schema(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/timetable_management/schemas.py"


def _is_seat_status_operations_schema(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/seat_status_operations/schemas.py"


def _is_srt_live_timetable_projection(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/timetable_management/srt_live_timetable.py")


def _is_korail_reservation_control_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/provider_adapters/korail_reservation_controls.py"
    )


def _is_korail_execution_owner(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_adapters/korail_execution.py"


PYDOLL_COMPATIBILITY_PATHS = frozenset(
    {
        "rail_waitlist/korail_pydoll_auth_contracts.py",
        "rail_waitlist/korail_pydoll_contracts.py",
        "rail_waitlist/korail_pydoll_page_safety.py",
        "rail_waitlist/korail_pydoll_reservation_contracts.py",
    }
)


def _is_korail_pydoll_contract_owner(relative_path: Path) -> bool:
    return relative_path.parent.as_posix() == "rail_waitlist/korail_sidecar/pydoll" and (
        relative_path.name in {"auth_contracts.py", "page_contracts.py", "reservation_contracts.py"}
    )


def _is_korail_pydoll_page_safety_owner(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/korail_sidecar/pydoll/page_safety.py"


def _is_production_module_outside_pydoll_contract_compatibility(relative_path: Path) -> bool:
    return relative_path.as_posix() not in PYDOLL_COMPATIBILITY_PATHS


def _is_srt_station_roster_owner(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_adapters/srt_station_roster.py"


def _is_srt_source_runtime_owner(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_adapters/srt_source_runtime.py"


def _is_tago_response_owner(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_adapters/tago_response.py"


def _is_tago_timetable_projection_owner(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/timetable_management/tago_timetable_projection.py"
    )


def _is_station_catalog_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/timetable_management/catalog_application.py")


def _is_station_catalog_compatibility(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/station_catalog_cache.py"


def _is_production_module_outside_station_catalog_compatibility(relative_path: Path) -> bool:
    return not _is_station_catalog_compatibility(relative_path)


def _is_station_visibility_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/timetable_management/station_visibility.py")


def _is_provider_execution_lease_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_execution/lease_application.py"


def _is_provider_execution_lease_compatibility(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/provider_execution_lease.py"


def _is_production_module_outside_provider_execution_lease_compatibility(
    relative_path: Path,
) -> bool:
    return relative_path.as_posix() != "rail_waitlist/provider_execution_lease.py"


def _is_stale_attempt_recovery_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/reservations/stale_attempt_recovery_application.py"
    )


def _is_watch_update_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/watch_management/update_application.py"


def _is_reservation_reconciliation_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/reconciliation_application.py")


def _is_reservation_reconciliation_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/reconciliation_policy.py")


def _is_reservation_reconciliation_state_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/reservations/reconciliation_state_application.py"
    )


def _is_reservation_reconciliation_state_runtime(relative_path: Path) -> bool:
    return relative_path.as_posix() == (
        "rail_waitlist/reservations/reconciliation_state_runtime.py"
    )


def _is_payment_hold_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/payment_hold_application.py")


def _is_reservation_attempt_policy(relative_path: Path) -> bool:
    return relative_path.as_posix() == "rail_waitlist/reservations/attempt_policy.py"


def _is_reservation_attempt_claim_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/attempt_claim_application.py")


def _is_reservation_attempt_result_application(relative_path: Path) -> bool:
    return relative_path.as_posix() == ("rail_waitlist/reservations/attempt_result_application.py")


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
        name="due provider selection is a pure ordered arming policy",
        matches=_is_due_provider_policy,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "models",
                "provider_adapters",
                "provider_registry",
                "providers",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="seat status operation schemas do not reverse-depend on runtime or transport",
        matches=_is_seat_status_operations_schema,
        forbidden_import_roots=frozenset(
            {
                "application",
                "auth",
                "config",
                "database",
                "fastapi",
                "http",
                "main",
                "models",
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
        name="reservation execution runtime only bridges a winner snapshot",
        matches=_is_reservation_execution_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "main",
                "metrics",
                "models",
                "observations",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "schemas",
                "services",
                "worker",
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
        name="observation group runtime owns lifecycle without global runtime wiring",
        matches=_is_observation_group_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "provider_registry",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="observation due runtime stays independent from worker globals",
        matches=_is_observation_due_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "provider_registry",
                "providers",
                "services",
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
        name="observation recording application receives runtime side effects",
        matches=_is_observation_recording_application,
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
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="observation status policy stays pure and provider neutral",
        matches=_is_observation_status_policy,
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
        name="idempotency model does not reverse-depend on application or legacy hubs",
        matches=_is_idempotency_model,
        forbidden_import_roots=frozenset(
            {"application", "auth", "fastapi", "models", "schemas", "services", "worker"}
        ),
    ),
    BoundaryRule(
        name="event stream schema does not reverse-depend on transport or legacy hubs",
        matches=_is_event_stream_schema,
        forbidden_import_roots=frozenset(
            {"database", "fastapi", "models", "schemas", "services", "sqlalchemy", "worker"}
        ),
    ),
    BoundaryRule(
        name="browser companion schemas own provider contracts without runtime dependencies",
        matches=_is_browser_companion_schema,
        forbidden_import_roots=frozenset(
            {
                "database",
                "celery",
                "celery_app",
                "config",
                "fastapi",
                "korail_browser_bridge",
                "models",
                "provider_registry",
                "provider_runtime",
                "providers",
                "schemas",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="browser companion models own persistence without legacy hub dependencies",
        matches=_is_browser_companion_model,
        forbidden_import_roots=frozenset(
            {
                "application",
                "celery",
                "celery_app",
                "config",
                "fastapi",
                "korail_browser_bridge",
                "models",
                "provider_registry",
                "provider_runtime",
                "providers",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider schema base stays independent from features and runtime",
        matches=_is_provider_schema_base,
        forbidden_import_roots=frozenset(
            {
                "browser_companion",
                "config",
                "database",
                "fastapi",
                "models",
                "providers",
                "schemas",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="official rail identity stays a pure provider-neutral helper",
        matches=_is_official_rail_identity,
        forbidden_import_roots=frozenset(
            {
                "browser_companion",
                "config",
                "database",
                "fastapi",
                "models",
                "pydantic",
                "providers",
                "schemas",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="official page confirmation schema owns transport without runtime dependencies",
        matches=_is_official_page_confirmation_schema,
        forbidden_import_roots=frozenset(
            {
                "application",
                "database",
                "fastapi",
                "models",
                "official_page_confirmations",
                "provider_runtime",
                "providers",
                "schemas",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="official page confirmation model does not reverse-depend on legacy hubs",
        matches=_is_official_page_confirmation_model,
        forbidden_import_roots=frozenset(
            {
                "application",
                "fastapi",
                "models",
                "official_page_confirmations",
                "provider_runtime",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="official page confirmation application owns persistence without runtime",
        matches=_is_official_page_confirmation_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "fastapi",
                "official_page_confirmations",
                "provider_runtime",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="official page confirmation compatibility module delegates to the feature owner",
        matches=_is_official_page_confirmation_compatibility,
        forbidden_import_roots=frozenset(
            {"database", "fastapi", "models", "schemas", "services", "sqlalchemy", "worker"}
        ),
    ),
    BoundaryRule(
        name="KORAIL reservation confirmation owner stays read-only and runtime independent",
        matches=_is_korail_provider_confirmation_owner,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "httpx",
                "korail_browser_automation",
                "korail_pydoll_browser",
                "korail_pydoll_confirmation_reader",
                "korail_reservation_confirmation",
                "korail_sidecar",
                "metrics",
                "models",
                "provider_adapters",
                "providers",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
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
        name="watch transition command owns only its locking transaction",
        matches=_is_watch_transition_command_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "outbox",
                "provider_accounts",
                "provider_adapters",
                "provider_registry",
                "provider_runtime",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="watch transition runtime composes feature dependencies without transport",
        matches=_is_watch_transition_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "provider_accounts",
                "provider_adapters",
                "provider_runtime",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="watch command runtime composes create and update dependencies without transport",
        matches=_is_watch_command_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "database",
                "fastapi",
                "metrics",
                "provider_accounts",
                "provider_adapters",
                "provider_runtime",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="watch create application owns persistence without runtime dependencies",
        matches=_is_watch_create_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
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
        name="watch arming application owns its UoW without worker runtime dependencies",
        matches=_is_watch_arming_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "outbox",
                "provider_registry",
                "provider_runtime",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider auth recovery application owns watch recovery policy only",
        matches=_is_provider_auth_recovery_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "outbox",
                "provider_accounts",
                "provider_registry",
                "provider_runtime",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider auth recovery runtime composes canonical feature owners",
        matches=_is_provider_auth_recovery_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "outbox",
                "provider_accounts",
                "provider_registry",
                "provider_runtime",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="admin auth schemas do not reverse-depend on legacy hubs or persistence",
        matches=_is_admin_auth_schema,
        forbidden_import_roots=frozenset({"auth", "database", "fastapi", "models", "schemas"}),
    ),
    BoundaryRule(
        name="admin auth models do not reverse-depend on transport or legacy hubs",
        matches=_is_admin_auth_model,
        forbidden_import_roots=frozenset({"auth", "fastapi", "models", "schemas"}),
    ),
    BoundaryRule(
        name="provider circuit application owns only circuit persistence",
        matches=_is_provider_circuit_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "notifications",
                "observations",
                "operations",
                "outbox",
                "provider_accounts",
                "provider_adapters",
                "provider_execution_lease",
                "provider_registry",
                "provider_runtime",
                "providers",
                "reservations",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider circuit model does not reverse-depend on application or legacy hubs",
        matches=_is_provider_circuit_model,
        forbidden_import_roots=frozenset(
            {
                "application",
                "celery",
                "config",
                "fastapi",
                "models",
                "operations",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="notification channel model does not reverse-depend on application or legacy hubs",
        matches=_is_notification_management_model,
        forbidden_import_roots=frozenset(
            {
                "application",
                "celery",
                "config",
                "delivery",
                "fastapi",
                "http",
                "models",
                "notifications",
                "schemas",
                "service",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider account model does not reverse-depend on application or legacy hubs",
        matches=_is_provider_account_management_model,
        forbidden_import_roots=frozenset(
            {
                "application",
                "auth",
                "celery",
                "config",
                "fastapi",
                "http",
                "models",
                "provider_accounts",
                "provider_runtime",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider account reservation adapter stays transaction-owner independent",
        matches=_is_provider_account_reservation_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "models",
                "provider_accounts",
                "provider_adapters",
                "provider_registry",
                "provider_runtime",
                "providers",
                "reservations",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider execution contracts stay persistence and runtime independent",
        matches=_is_provider_execution_contract,
        forbidden_import_roots=frozenset(
            {
                "application",
                "celery",
                "config",
                "database",
                "fastapi",
                "models",
                "provider_adapters",
                "provider_registry",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider execution model does not reverse-depend on application or legacy hubs",
        matches=_is_provider_execution_model,
        forbidden_import_roots=frozenset(
            {
                "application",
                "celery",
                "config",
                "fastapi",
                "models",
                "provider_execution_lease",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider execution lease application owns persistence without runtime wiring",
        matches=_is_provider_execution_lease_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "provider_adapters",
                "provider_execution_lease",
                "provider_registry",
                "provider_runtime",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="provider execution lifecycle policy stays runtime-shell independent",
        matches=_is_provider_execution_lifecycle_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "models",
                "provider_adapters",
                "provider_registry",
                "provider_runtime",
                "providers",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="timetable model does not reverse-depend on application or legacy hubs",
        matches=_is_timetable_management_model,
        forbidden_import_roots=frozenset(
            {
                "application",
                "celery",
                "config",
                "fastapi",
                "http",
                "models",
                "operations",
                "provider_adapters",
                "schemas",
                "services",
                "station_catalog_cache",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="SRT live timetable projection stays application and runtime independent",
        matches=_is_srt_live_timetable_projection,
        forbidden_import_roots=frozenset(
            {
                "application",
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "http",
                "main",
                "metrics",
                "models",
                "provider_registry",
                "provider_runtime",
                "services",
                "srt_live_timetable",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="KORAIL reservation control policy stays runtime-shell independent",
        matches=_is_korail_reservation_control_policy,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "korail_pydoll_reservation_driver",
                "korail_reservation_controls",
                "main",
                "metrics",
                "models",
                "provider_registry",
                "provider_runtime",
                "providers",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="KORAIL execution owner does not reverse-depend on runtime composition",
        matches=_is_korail_execution_owner,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "database",
                "fastapi",
                "korail_execution",
                "main",
                "metrics",
                "models",
                "operations",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="SRT station roster stays consumer and runtime-shell independent",
        matches=_is_srt_station_roster_owner,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "main",
                "metrics",
                "models",
                "operations",
                "provider_registry",
                "provider_runtime",
                "providers",
                "services",
                "srt_reservation",
                "srt_seat_source",
                "srt_station_roster",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="SRT source runtime does not reverse-depend on composition shells",
        matches=_is_srt_source_runtime_owner,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "database",
                "fastapi",
                "main",
                "metrics",
                "models",
                "operations",
                "provider_registry",
                "providers",
                "services",
                "srt_execution",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="TAGO response parser stays transport and runtime independent",
        matches=_is_tago_response_owner,
        forbidden_import_roots=frozenset(
            {
                "application",
                "asyncio",
                "catalog_application",
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "http",
                "httpx",
                "main",
                "metrics",
                "models",
                "operations",
                "provider_registry",
                "providers",
                "services",
                "tago",
                "timetable",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="TAGO timetable projection stays transport, cache, and runtime independent",
        matches=_is_tago_timetable_projection_owner,
        forbidden_import_roots=frozenset(
            {
                "application",
                "asyncio",
                "catalog_application",
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "http",
                "httpx",
                "main",
                "metrics",
                "models",
                "operations",
                "provider_adapters",
                "provider_registry",
                "provider_runtime",
                "providers",
                "redis",
                "services",
                "station_catalog_cache",
                "tago",
                "tago_response",
                "time",
                "timetable_snapshot_cache",
                "timetable_support",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="timetable schema owns transport without runtime or legacy dependencies",
        matches=_is_timetable_management_schema,
        forbidden_import_roots=frozenset(
            {
                "catalog_application",
                "config",
                "database",
                "fastapi",
                "main",
                "models",
                "provider_adapters",
                "providers",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="station catalog application owns its UoW without runtime wiring",
        matches=_is_station_catalog_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "fastapi",
                "main",
                "metrics",
                "operations",
                "services",
                "station_catalog_cache",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="production modules use the canonical station catalog application owner",
        matches=_is_production_module_outside_station_catalog_compatibility,
        forbidden_import_roots=frozenset({"station_catalog_cache"}),
    ),
    BoundaryRule(
        name="station visibility owns discoverability policy without runtime wiring",
        matches=_is_station_visibility_policy,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "database",
                "fastapi",
                "main",
                "metrics",
                "models",
                "operations",
                "services",
                "sqlalchemy",
                "station_catalog_cache",
                "station_visibility",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="production modules use the canonical provider execution owner",
        matches=_is_production_module_outside_provider_execution_lease_compatibility,
        forbidden_import_roots=frozenset({"provider_execution_lease"}),
    ),
    BoundaryRule(
        name="KORAIL Pydoll contract owners stay leaf-like",
        matches=_is_korail_pydoll_contract_owner,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "httpx",
                "metrics",
                "models",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "pydantic",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="KORAIL Pydoll page safety stays transport and runtime independent",
        matches=_is_korail_pydoll_page_safety_owner,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "httpx",
                "korail_pydoll_browser",
                "metrics",
                "models",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "services",
                "sqlalchemy",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="production modules use canonical KORAIL Pydoll owners",
        matches=_is_production_module_outside_pydoll_contract_compatibility,
        forbidden_import_roots=frozenset(
            {
                "korail_pydoll_auth_contracts",
                "korail_pydoll_contracts",
                "korail_pydoll_page_safety",
                "korail_pydoll_reservation_contracts",
            }
        ),
    ),
    BoundaryRule(
        name="stale attempt recovery application owns its recovery transaction",
        matches=_is_stale_attempt_recovery_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "observations",
                "outbox",
                "provider_accounts",
                "provider_adapters",
                "provider_registry",
                "providers",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="watch lookup joins caller UoW without transport or runtime dependencies",
        matches=_is_watch_lookup_application,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "http",
                "idempotency",
                "main",
                "metrics",
                "outbox",
                "provider_adapters",
                "provider_registry",
                "providers",
                "schemas",
                "services",
                "worker",
            }
        ),
    ),
    BoundaryRule(
        name="watch schemas do not reverse-depend on transport, runtime, or persistence",
        matches=_is_watch_management_schema,
        forbidden_import_roots=frozenset(
            {
                "application",
                "auth",
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "http",
                "main",
                "models",
                "provider_adapters",
                "provider_registry",
                "providers",
                "services",
                "sqlalchemy",
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
        forbidden_import_roots=PROVIDER_APPLICATION_FORBIDDEN_IMPORT_ROOTS
        | frozenset({"services"}),
    ),
    BoundaryRule(
        name="reservation reconciliation policy stays pure and runtime independent",
        matches=_is_reservation_reconciliation_policy,
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
        name="reservation reconciliation state receives runtime side effects",
        matches=_is_reservation_reconciliation_state_application,
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
        name="reservation reconciliation state runtime stays transport independent",
        matches=_is_reservation_reconciliation_state_runtime,
        forbidden_import_roots=frozenset(
            {
                "celery",
                "celery_app",
                "config",
                "database",
                "fastapi",
                "metrics",
                "notification_management",
                "provider_adapters",
                "provider_registry",
                "providers",
                "services",
                "worker",
            }
        ),
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
        name="reservation attempt result application receives runtime side effects",
        matches=_is_reservation_attempt_result_application,
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
        forbidden_import_roots=frozenset({"providers"}),
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


@pytest.mark.parametrize(
    ("relative_path", "symbol", "canonical_module", "canonical_level"),
    [
        ("rail_waitlist/event_stream/http.py", "EventRead", "schemas", 1),
        (
            "rail_waitlist/idempotency/application.py",
            "IdempotencyRecord",
            "models",
            1,
        ),
    ],
)
def test_event_and_idempotency_consumers_use_their_feature_owner(
    relative_path: str,
    symbol: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


@pytest.mark.parametrize(
    ("relative_path", "symbol", "canonical_module", "canonical_level"),
    [
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionChallenge",
            "models",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionCredential",
            "models",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionPairing",
            "models",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "KorailBrowserSeatSnapshot",
            "models",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "KorailBrowserSnapshotBatch",
            "models",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionChallengeCreate",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionChallengeRead",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionCredentialRead",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionPairingCreate",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionPairingExchange",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionPairingRead",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionPairingResult",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "BrowserCompanionStatus",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "KorailBrowserSnapshotCreate",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/http.py",
            "KorailBrowserSnapshotRead",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/browser_companion/snapshot_overlay.py",
            "KorailBrowserSeatSnapshot",
            "models",
            1,
        ),
        (
            "rail_waitlist/browser_companion/snapshot_overlay.py",
            "KorailBrowserSnapshotBatch",
            "models",
            1,
        ),
        (
            "rail_waitlist/browser_companion/snapshot_overlay.py",
            "KORAIL_BROWSER_COMPANION_SOURCE",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/timetable_management/official_evidence_http.py",
            "KorailBrowserSnapshotBatch",
            "browser_companion.models",
            2,
        ),
        (
            "rail_waitlist/timetable_management/official_evidence_http.py",
            "KorailBrowserSnapshotRevision",
            "browser_companion.schemas",
            2,
        ),
    ],
)
def test_browser_companion_consumers_use_the_feature_owner(
    relative_path: str,
    symbol: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


def test_browser_bridge_consumers_use_exact_canonical_owners_without_legacy_reentry() -> None:
    expected_imports = {
        "rail_waitlist/main.py": {
            ("browser_companion.http", 1, "admin_router", "browser_companion_admin_router"),
            ("browser_companion.http", 1, "router", "browser_bridge_router"),
        },
        "rail_waitlist/timetable_management/application.py": {
            (
                "browser_companion.snapshot_overlay",
                2,
                "overlay_korail_browser_snapshots",
                None,
            ),
        },
    }
    canonical_owners = {
        ("rail_waitlist", "browser_companion", "http"): {"admin_router", "router"},
        ("rail_waitlist", "browser_companion", "snapshot_overlay"): {
            "overlay_korail_browser_snapshots"
        },
    }
    actual_imports: dict[str, set[tuple[str | None, int, str, str | None]]] = {}
    canonical_consumers = {owner: set() for owner in canonical_owners}
    legacy_consumers: set[str] = set()
    legacy_path = "rail_waitlist/korail_browser_bridge.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        relative_name = relative_path.as_posix()
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
        selected_imports = {
            (node.module, node.level, alias.name, alias.asname)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module, node.level)
            in {
                ("browser_companion.http", 1),
                ("browser_companion.snapshot_overlay", 2),
            }
            for alias in node.names
        }
        if selected_imports and relative_name != legacy_path:
            actual_imports[relative_name] = selected_imports

        if relative_name != legacy_path and _module_contract_references(
            source,
            relative_path,
            owner=("rail_waitlist", "korail_browser_bridge"),
            symbols={"admin_router", "router", "overlay_korail_browser_snapshots"},
        ):
            legacy_consumers.add(relative_name)

        for owner, symbols in canonical_owners.items():
            if relative_name in {
                legacy_path,
                f"{'/'.join(owner)}.py",
            }:
                continue
            if _module_contract_references(
                source,
                relative_path,
                owner=owner,
                symbols=symbols,
            ):
                canonical_consumers[owner].add(relative_name)

    assert actual_imports == expected_imports
    assert canonical_consumers == {
        ("rail_waitlist", "browser_companion", "http"): {"rail_waitlist/main.py"},
        ("rail_waitlist", "browser_companion", "snapshot_overlay"): {
            "rail_waitlist/timetable_management/application.py"
        },
    }
    assert legacy_consumers == set()


@pytest.mark.parametrize(
    ("relative_path", "canonical_module", "canonical_level"),
    [
        ("rail_waitlist/provider_circuit/application.py", "models", 1),
        ("rail_waitlist/services.py", "provider_circuit.models", 1),
        ("rail_waitlist/operations.py", "provider_circuit.models", 1),
        (
            "rail_waitlist/observations/group_application.py",
            "provider_circuit.models",
            2,
        ),
        (
            "rail_waitlist/reservations/execution_application.py",
            "provider_circuit.models",
            2,
        ),
    ],
)
def test_provider_circuit_consumers_use_the_feature_model_owner(
    relative_path: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "ProviderCircuit" for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


@pytest.mark.parametrize(
    ("relative_path", "canonical_module", "canonical_level"),
    [
        ("rail_waitlist/notification_management/service.py", "models", 1),
        ("rail_waitlist/notification_management/http.py", "models", 1),
        ("rail_waitlist/notification_management/delivery.py", "models", 1),
        (
            "rail_waitlist/notification_management/watch_transition_application.py",
            "models",
            1,
        ),
        (
            "rail_waitlist/watch_management/update_application.py",
            "notification_management.models",
            2,
        ),
    ],
)
def test_notification_channel_consumers_use_the_feature_model_owner(
    relative_path: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "NotificationChannel" for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


def test_watch_update_only_depends_on_the_notification_management_model() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "update_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    feature_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("notification_management")
    }

    assert feature_imports == {("notification_management.models", 2)}


def test_central_models_exposes_notification_models_as_canonical_module_aliases() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    module_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname == "notification_management_models"
    }
    aliases = {
        (
            node.targets[0].id,
            node.value.value.id,
            node.value.attr,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.targets[0].id
        in {"NotificationChannel", "NativePushPairing", "NativePushCredential"}
    }

    assert module_imports == {
        ("notification_management", 1, "models", "notification_management_models")
    }
    assert aliases == {
        ("NotificationChannel", "notification_management_models", "NotificationChannel"),
        ("NativePushPairing", "notification_management_models", "NativePushPairing"),
        ("NativePushCredential", "notification_management_models", "NativePushCredential"),
    }


@pytest.mark.parametrize(
    ("relative_path", "canonical_module", "canonical_level"),
    [
        (
            "rail_waitlist/provider_account_management/application.py",
            "models",
            1,
        ),
        (
            "rail_waitlist/provider_account_management/runtime.py",
            "models",
            1,
        ),
        (
            "rail_waitlist/observations/group_application.py",
            "provider_account_management.models",
            2,
        ),
        (
            "rail_waitlist/reservations/execution_application.py",
            "provider_account_management.models",
            2,
        ),
        (
            "rail_waitlist/reservations/reconciliation_application.py",
            "provider_account_management.models",
            2,
        ),
    ],
)
def test_provider_account_consumers_use_the_feature_model_owner(
    relative_path: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "RailProviderAccount" for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


def test_central_models_exposes_provider_account_as_a_canonical_module_alias() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    module_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname == "provider_account_management_models"
    }
    aliases = {
        (
            node.targets[0].id,
            node.value.value.id,
            node.value.attr,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.targets[0].id == "RailProviderAccount"
    }

    assert module_imports == {
        ("provider_account_management", 1, "models", "provider_account_management_models")
    }
    assert aliases == {
        ("RailProviderAccount", "provider_account_management_models", "RailProviderAccount")
    }


@pytest.mark.parametrize(
    ("relative_path", "symbol", "canonical_module", "canonical_level"),
    [
        (
            "rail_waitlist/observations/recording_application.py",
            "SEAT_FOUND_STATUSES",
            "status_policy",
            1,
        ),
        (
            "rail_waitlist/observations/recording_application.py",
            "ACTIONABLE_SEAT_STATUSES",
            "status_policy",
            1,
        ),
        (
            "rail_waitlist/observations/group_application.py",
            "SEAT_FOUND_STATUSES",
            "status_policy",
            1,
        ),
        (
            "rail_waitlist/observations/group_application.py",
            "ACTIONABLE_SEAT_STATUSES",
            "status_policy",
            1,
        ),
        (
            "rail_waitlist/services.py",
            "SEAT_FOUND_STATUSES",
            "observations.status_policy",
            1,
        ),
        (
            "rail_waitlist/services.py",
            "ACTIONABLE_SEAT_STATUSES",
            "observations.status_policy",
            1,
        ),
    ],
)
def test_observation_status_consumers_use_the_canonical_policy_owner(
    relative_path: str,
    symbol: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


@pytest.mark.parametrize(
    ("symbol", "canonical_module", "canonical_level"),
    [
        ("WatchArmingDependencies", "watch_management.arming_application", 1),
        (
            "arm_supported_provider_watches",
            "watch_management.arming_application",
            1,
        ),
    ],
)
def test_worker_uses_the_watch_arming_application_owner(
    symbol: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


def test_worker_arming_wrapper_no_longer_owns_sql_or_watch_status_policy() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    arming_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_arm_supported_provider_watches"
    )
    called_names = {
        node.func.id
        for node in ast.walk(arming_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    sqlalchemy_select_imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy"
        for alias in node.names
    }
    watch_status_imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "domain" and node.level == 1
        for alias in node.names
    }

    assert "select" not in called_names
    assert "select" not in sqlalchemy_select_imports
    assert "WatchStatus" not in watch_status_imports


@pytest.mark.parametrize(
    "symbol",
    ["WatchGroupRuntimeDependencies", "process_watch_group_runtime"],
)
def test_worker_uses_the_observation_group_runtime_owner(symbol: str) -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {("observations.group_runtime", 1)}


def test_worker_watch_group_wrapper_no_longer_owns_runtime_lifecycle() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_process_watch_group"
    )
    called_names = {
        node.func.id
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert called_names == {
        "WatchGroupRuntimeDependencies",
        "process_watch_group_runtime_application",
    }


def test_worker_reservation_wrapper_only_composes_the_canonical_runtime() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_reserve_winner"
    )
    called_names = [
        node.func.id
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    execution_imports = {
        (node.module, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module
        in {
            "reservations.execution_application",
            "reservations.execution_runtime",
        }
        for alias in node.names
    }

    assert called_names.count("reserve_observation_winner_application") == 1
    assert called_names.count("_reservation_execution_dependencies") == 1
    assert "ReservationExecutionTarget" not in {
        alias_name for _, alias_name, _ in execution_imports
    }
    assert "execute_reservation" not in {alias_name for _, alias_name, _ in execution_imports}
    assert (
        "reservations.execution_runtime",
        "reserve_observation_winner",
        "reserve_observation_winner_application",
    ) in execution_imports


def test_worker_due_sweep_delegates_to_the_canonical_runtime() -> None:
    worker_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    worker_tree = ast.parse(
        worker_path.read_text(encoding="utf-8"),
        filename=str(worker_path),
    )
    wrapper = next(
        node
        for node in worker_tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_process_due_watches"
    )
    dependency_factory = next(
        node
        for node in worker_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_due_sweep_runtime_dependencies"
    )
    wrapper_calls = [
        node.func.id
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    dependency_names = {
        node.id
        for node in ast.walk(dependency_factory)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    provider_literals = {
        node.attr
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Provider"
    }
    owner_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in worker_tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in {"DueSweepRuntimeDependencies", "process_due_watches"}
    }

    assert wrapper_calls == ["process_due_watches_runtime", "_due_sweep_runtime_dependencies"]
    assert {
        "DueSweepRuntimeDependencies",
        "get_settings",
        "korail_background_monitoring_enabled",
        "select_provider_arm_targets_policy",
        "process_due_pipeline",
        "_due_pipeline_dependencies",
        "WATCH_GROUPS",
    } <= dependency_names
    assert provider_literals == set()
    assert owner_imports == {
        (
            "observations.due_runtime",
            1,
            "DueSweepRuntimeDependencies",
            None,
        ),
        (
            "observations.due_runtime",
            1,
            "process_due_watches",
            "process_due_watches_runtime",
        ),
    }


def test_due_sweep_runtime_has_an_exact_import_and_call_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "observations" / "due_runtime.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import)]
    imports_from = {
        (node.module, node.level) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_due_watches"
    )
    dependency_calls = [
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "dependencies"
    ]

    assert direct_imports == []
    assert imports_from == {
        ("__future__", 0),
        ("collections.abc", 0),
        ("dataclasses", 0),
        ("typing", 0),
        ("domain", 2),
        ("due_pipeline_application", 1),
    }
    assert len(dependency_calls) == 5
    assert set(dependency_calls) == {
        "select_provider_arm_targets",
        "korail_background_enabled",
        "process_due_pipeline",
        "due_pipeline_dependencies",
        "record_group_count",
    }


def test_reservation_execution_runtime_does_not_own_adapter_or_lease_lifecycle() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservations" / "execution_runtime.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    runtime = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "reserve_observation_winner"
    )
    direct_calls = [
        node.func.id
        for node in ast.walk(runtime)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    attribute_calls = {
        node.func.attr
        for node in ast.walk(runtime)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert direct_calls.count("ReservationExecutionTarget") == 1
    assert direct_calls.count("execute_reservation") == 1
    assert attribute_calls.isdisjoint(
        {"acquire", "release", "drain", "close", "dispose", "commit", "rollback"}
    )


@pytest.mark.parametrize(
    ("relative_path", "symbol", "canonical_module", "canonical_level"),
    [
        (
            "rail_waitlist/timetable_management/application.py",
            "overlay_official_page_confirmations",
            "official_page_confirmation.application",
            2,
        ),
        (
            "rail_waitlist/timetable_management/official_evidence_http.py",
            "upsert_official_page_confirmations",
            "official_page_confirmation.application",
            2,
        ),
        (
            "rail_waitlist/timetable_management/official_evidence_http.py",
            "OfficialPageSeatConfirmationCreate",
            "official_page_confirmation.schemas",
            2,
        ),
        (
            "rail_waitlist/timetable_management/official_evidence_http.py",
            "OfficialPageSeatConfirmationRead",
            "official_page_confirmation.schemas",
            2,
        ),
        (
            "rail_waitlist/official_page_confirmation/application.py",
            "OfficialPageSeatConfirmation",
            "models",
            1,
        ),
        (
            "rail_waitlist/official_page_confirmation/application.py",
            "OFFICIAL_PAGE_CONFIRMATION_SOURCE",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/official_page_confirmation/application.py",
            "OfficialPageSeatConfirmationCreate",
            "schemas",
            1,
        ),
        (
            "rail_waitlist/official_page_confirmations.py",
            "overlay_official_page_confirmations",
            "official_page_confirmation.application",
            1,
        ),
        (
            "rail_waitlist/official_page_confirmations.py",
            "upsert_official_page_confirmations",
            "official_page_confirmation.application",
            1,
        ),
    ],
)
def test_official_page_confirmation_consumers_use_the_bounded_context_owner(
    relative_path: str,
    symbol: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


def test_official_page_confirmation_application_does_not_import_central_model_hub() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "official_page_confirmation" / "application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    central_model_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "models" and node.level == 2
    }

    assert central_model_imports == set()


@pytest.mark.parametrize(
    ("relative_path", "canonical_level"),
    [
        ("rail_waitlist/browser_companion/schemas.py", 2),
        ("rail_waitlist/official_page_confirmation/application.py", 2),
        ("rail_waitlist/official_page_confirmation/schemas.py", 2),
        ("rail_waitlist/timetable_evidence.py", 1),
        ("rail_waitlist/watch_management/create_application.py", 2),
    ],
)
def test_official_train_number_consumers_use_the_provider_neutral_owner(
    relative_path: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "normalize_official_train_number" for alias in node.names)
    }

    assert owner_imports == {("official_rail_identity", canonical_level)}


@pytest.mark.parametrize(
    ("symbol", "canonical_module"),
    [
        ("contains_protection_marker", "official_rail_identity"),
        ("ProviderContractModel", "provider_schema_base"),
    ],
)
def test_browser_companion_schema_uses_shared_contract_owners(
    symbol: str,
    canonical_module: str,
) -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "browser_companion" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {(canonical_module, 2)}


@pytest.mark.parametrize(
    ("symbol", "canonical_module"),
    [
        ("contains_protection_marker", "official_rail_identity"),
        ("normalize_official_train_number", "official_rail_identity"),
        ("ProviderContractModel", "provider_schema_base"),
    ],
)
def test_official_page_confirmation_schema_uses_neutral_contract_owners(
    symbol: str,
    canonical_module: str,
) -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "official_page_confirmation" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {(canonical_module, 2)}


def test_official_rail_identity_imports_only_language_dependencies() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "official_rail_identity.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    import_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_roots.add(node.module.split(".")[0])

    assert import_roots <= {"__future__"}


def test_provider_schema_base_imports_only_schema_dependencies() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_schema_base.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    import_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_roots.add(node.module.split(".")[0])

    assert import_roots <= {"pydantic", "schema_base"}


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


def test_observation_recording_application_joins_the_callers_unit_of_work() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "observations" / "recording_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "flush" in called_attributes
    assert called_attributes.isdisjoint(
        {"begin", "begin_nested", "commit", "refresh", "rollback", "with_for_update"}
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


def test_watch_transition_command_application_owns_only_its_command_transaction() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "watch_management" / "transition_command_application.py"
    )
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
    assert {"with_for_update", "commit", "refresh"} <= called_attributes
    assert called_attributes.isdisjoint({"begin", "begin_nested", "flush", "rollback"})


@pytest.mark.parametrize(
    ("relative_path", "legacy_symbol"),
    [
        ("rail_waitlist/watch_management/http.py", "transition_watch"),
        ("rail_waitlist/worker.py", "apply_watch_transition"),
        (
            "rail_waitlist/provider_account_management/application.py",
            "resume_watches_after_verified_provider_login",
        ),
        (
            "rail_waitlist/provider_account_management/runtime.py",
            "resume_watches_after_verified_provider_login",
        ),
    ],
)
def test_production_transition_wiring_does_not_import_legacy_service_symbol(
    relative_path: str,
    legacy_symbol: str,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    legacy_service_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and "services" in node.module.split(".")
        for alias in node.names
    }

    assert legacy_symbol not in legacy_service_imports


@pytest.mark.parametrize(
    ("relative_path", "symbol", "canonical_module"),
    [
        ("rail_waitlist/auth.py", "AdminAccount", "admin_auth.models"),
        ("rail_waitlist/auth.py", "AdminSession", "admin_auth.models"),
        ("rail_waitlist/auth.py", "AuthStatus", "admin_auth.schemas"),
        ("rail_waitlist/auth.py", "LoginResult", "admin_auth.schemas"),
        (
            "rail_waitlist/auth.py",
            "UsernamePasswordCredentials",
            "admin_auth.schemas",
        ),
        (
            "rail_waitlist/observations/cycle_application.py",
            "AdminAccount",
            "admin_auth.models",
        ),
        (
            "rail_waitlist/ui_preferences/application.py",
            "AdminAccount",
            "admin_auth.models",
        ),
        (
            "rail_waitlist/ui_preferences/http.py",
            "AdminAccount",
            "admin_auth.models",
        ),
    ],
)
def test_production_admin_auth_consumers_use_the_canonical_owner(
    relative_path: str,
    symbol: str,
    canonical_module: str,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_modules == {canonical_module}


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


def test_watch_lookup_application_joins_the_callers_unit_of_work() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "lookup_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]

    assert "HTTPException" not in called_names
    assert called_attributes.count("get") == 1
    assert set(called_attributes).isdisjoint(
        {
            "add",
            "begin",
            "begin_nested",
            "commit",
            "delete",
            "flush",
            "refresh",
            "rollback",
            "with_for_update",
        }
    )


def test_watch_lookup_consumers_use_canonical_owner_and_legacy_wrapper_has_no_query() -> None:
    http_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "http.py"
    http_tree = ast.parse(http_path.read_text(encoding="utf-8"), filename=str(http_path))
    lookup_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(http_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in {"WatchLookupNotFound", "find_watch"}
    }
    legacy_find_imports = {
        alias.name
        for node in ast.walk(http_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "services"
        for alias in node.names
        if alias.name == "find_watch"
    }
    lookup_calls = {
        node.func.id
        for node in ast.walk(http_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert lookup_imports == {
        ("lookup_application", 1, "WatchLookupNotFound", None),
        ("lookup_application", 1, "find_watch", "find_watch_application"),
    }
    assert legacy_find_imports == set()
    assert "_find_watch_or_404" in lookup_calls

    services_path = SOURCE_ROOT / "rail_waitlist" / "services.py"
    services_tree = ast.parse(
        services_path.read_text(encoding="utf-8"), filename=str(services_path)
    )
    find_definition = next(
        node
        for node in services_tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "find_watch"
    )
    service_called_names = {
        node.func.id
        for node in ast.walk(find_definition)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    service_called_attributes = {
        node.func.attr
        for node in ast.walk(find_definition)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "find_watch_application" in service_called_names
    assert "get" not in service_called_attributes


def test_production_does_not_reintroduce_the_legacy_watch_lookup() -> None:
    legacy_owner = ("rail_waitlist", "services")
    violations: list[str] = []

    def attribute_path(node: ast.AST) -> str | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        return ".".join(reversed(parts))

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == "rail_waitlist/services.py":
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        module_bindings: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if tuple(alias.name.split(".")) == legacy_owner:
                        module_bindings.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    resolved = tuple((node.module or "").split("."))
                else:
                    keep = len(package_parts) - (node.level - 1)
                    resolved = (*package_parts[:keep], *((node.module or "").split(".")))
                if resolved == legacy_owner and any(
                    alias.name == "find_watch" for alias in node.names
                ):
                    violations.append(f"{relative_path.as_posix()}:{node.lineno}: direct")
                if resolved == ("rail_waitlist",):
                    module_bindings.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "services"
                    )

        legacy_calls = {f"{binding}.find_watch" for binding in module_bindings} | {
            "rail_waitlist.services.find_watch"
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and attribute_path(node) in legacy_calls:
                violations.append(f"{relative_path.as_posix()}:{node.lineno}: module")

    assert violations == []


def test_watch_create_application_owns_only_its_create_transaction() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "create_application.py"
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
    assert {"flush", "commit", "rollback", "refresh"} <= called_attributes
    assert called_attributes.isdisjoint({"begin", "begin_nested", "with_for_update"})


def test_provider_auth_recovery_application_joins_the_account_unit_of_work() -> None:
    module_path = (
        SOURCE_ROOT
        / "rail_waitlist"
        / "provider_account_management"
        / "auth_recovery_application.py"
    )
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
    assert "with_for_update" in called_attributes
    assert called_attributes.isdisjoint(
        {"begin", "begin_nested", "commit", "flush", "refresh", "rollback"}
    )


def test_provider_circuit_application_joins_the_callers_unit_of_work() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_circuit" / "application.py"
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
    assert {"begin_nested", "flush", "with_for_update"} <= called_attributes
    assert called_attributes.isdisjoint({"begin", "commit", "refresh", "rollback"})


def test_stale_attempt_recovery_application_owns_only_its_recovery_transaction() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "reservations" / "stale_attempt_recovery_application.py"
    )
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
    assert {"with_for_update", "commit"} <= called_attributes
    assert called_attributes.isdisjoint({"begin", "begin_nested", "refresh", "rollback"})


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


def test_reservation_attempt_result_application_joins_the_callers_unit_of_work() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservations" / "attempt_result_application.py"
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
    assert called_attributes.isdisjoint(
        {"begin", "begin_nested", "commit", "flush", "refresh", "rollback", "with_for_update"}
    )


def test_reservation_reconciliation_state_joins_the_callers_unit_of_work() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "reservations" / "reconciliation_state_application.py"
    )
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
    assert called_attributes.isdisjoint(
        {"begin", "begin_nested", "commit", "flush", "refresh", "rollback", "with_for_update"}
    )


def test_reconciliation_state_runtime_delegates_without_owning_the_unit_of_work() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservations" / "reconciliation_state_runtime.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    runtime = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "apply_reservation_reconciliation"
    )
    called_names = {
        node.func.id
        for node in ast.walk(runtime)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(runtime)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert called_names == {
        "apply_reservation_reconciliation_application",
        "reservation_reconciliation_state_dependencies",
    }
    assert called_attributes.isdisjoint(
        {"begin", "begin_nested", "commit", "flush", "refresh", "rollback", "with_for_update"}
    )


def test_reconciliation_orchestrator_uses_canonical_policy_and_state_owners() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservations" / "reconciliation_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    reservation_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("reconciliation_")
    }

    assert reservation_imports == {
        "reconciliation_policy",
        "reconciliation_state_application",
    }


def test_observation_group_imports_only_canonical_reservation_contracts() -> None:
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

    assert reservation_imports == {
        "reservations.attempt_policy",
        "reservations.payment_hold_retry_application",
    }


def test_provider_execution_lease_facade_only_reexports_canonical_owner_symbols() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_execution_lease.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules == {
        "provider_execution.contracts",
        "provider_execution.lease_application",
    }


def test_worker_execution_lease_wrapper_only_composes_the_canonical_owner() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_acquire_execution_lease"
    )
    called_names = {
        node.func.id
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    direct_imports = {
        alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert called_names == {
        "ExecutionLeaseAcquisitionDependencies",
        "acquire_anonymous_public_execution_lease",
    }
    assert {"timedelta", "uuid4"}.isdisjoint(direct_imports)


@pytest.mark.parametrize(
    ("relative_path", "symbol", "canonical_module", "canonical_level"),
    [
        (
            "rail_waitlist/observations/group_runtime.py",
            "AcquireExecutionLease",
            "provider_execution.contracts",
            2,
        ),
        (
            "rail_waitlist/observations/group_runtime.py",
            "ExecutionLeaseGrant",
            "provider_execution.contracts",
            2,
        ),
        (
            "rail_waitlist/observations/group_runtime.py",
            "ExecutionLeaseService",
            "provider_execution.contracts",
            2,
        ),
        (
            "rail_waitlist/reservations/reconciliation_application.py",
            "AcquireExecutionLease",
            "provider_execution.contracts",
            2,
        ),
        (
            "rail_waitlist/reservations/reconciliation_application.py",
            "ExecutionLeaseGrant",
            "provider_execution.contracts",
            2,
        ),
        (
            "rail_waitlist/reservations/reconciliation_application.py",
            "lock_execution_lease_current",
            "provider_execution.lease_application",
            2,
        ),
        (
            "rail_waitlist/ui_preferences/application.py",
            "ProviderExecutionLease",
            "provider_execution.models",
            2,
        ),
        (
            "rail_waitlist/ui_preferences/application.py",
            "ANONYMOUS_PUBLIC_ACCOUNT_SCOPE",
            "provider_execution.lease_application",
            2,
        ),
    ],
)
def test_provider_execution_consumers_use_canonical_owner_paths(
    relative_path: str,
    symbol: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


def test_worker_lifecycle_wrappers_delegate_to_canonical_owner_without_local_error_policy() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (alias.name, node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name
        in {
            "close_execution_adapter_safely",
            "drain_execution_adapter_safely",
        }
    }
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert imports == {
        (
            "close_execution_adapter_safely",
            "provider_execution.lifecycle_runtime",
            1,
        ),
        (
            "drain_execution_adapter_safely",
            "provider_execution.lifecycle_runtime",
            1,
        ),
    }
    for wrapper_name, canonical_name in (
        ("_close_execution_adapter", "close_execution_adapter_safely"),
        ("_drain_execution_adapter", "drain_execution_adapter_safely"),
    ):
        function = functions[wrapper_name]
        assert not any(isinstance(node, ast.Try) for node in ast.walk(function))
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == canonical_name
        ]
        assert len(calls) == 1
        assert [keyword.arg for keyword in calls[0].keywords] == ["logger"]


def test_worker_reservation_auth_wrapper_delegates_to_canonical_transaction_adapter() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (alias.name, node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "update_provider_auth_status_in_reservation_transaction"
    }
    wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_update_provider_auth_status_in_reservation_transaction"
    )
    canonical_calls = [
        node
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "update_provider_auth_status_in_reservation_transaction"
    ]

    assert imports == {
        (
            "update_provider_auth_status_in_reservation_transaction",
            "provider_account_management.reservation_runtime",
            1,
        )
    }
    assert len(canonical_calls) == 1
    assert [keyword.arg for keyword in canonical_calls[0].keywords] == [
        "expected_credential_version",
        "persist_auth_status",
    ]
    assert not any(isinstance(node, ast.Try) for node in ast.walk(wrapper))


def test_provider_account_reservation_adapter_joins_outer_transaction_without_legacy_import() -> (
    None
):
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "provider_account_management" / "reservation_runtime.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    persistence_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "persist_auth_status"
    )
    keywords = {keyword.arg: keyword.value for keyword in persistence_call.keywords}

    assert called_attributes.isdisjoint(
        {"begin", "begin_nested", "commit", "flush", "refresh", "rollback", "with_for_update"}
    )
    assert isinstance(keywords["commit"], ast.Constant)
    assert keywords["commit"].value is False
    assert isinstance(keywords["expected_credential_version"], ast.Name)
    assert keywords["expected_credential_version"].id == "expected_credential_version"


@pytest.mark.parametrize(
    ("relative_path", "canonical_module", "canonical_level"),
    [
        ("rail_waitlist/operations.py", "timetable_management.models", 1),
        ("rail_waitlist/timetable_management/catalog_application.py", "models", 1),
    ],
)
def test_station_catalog_consumers_use_canonical_model_owner(
    relative_path: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "StationCatalogCache" for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


def test_all_production_station_catalog_model_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "timetable_management", "models")
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        if module_parts[-1] == "__init__":
            package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not any(
                alias.name == "StationCatalogCache" for alias in node.names
            ):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )

    assert violations == []


def test_central_station_catalog_model_is_an_exact_alias_without_redeclaration() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert "StationCatalogCache" not in class_names
    value = aliases["StationCatalogCache"]
    assert isinstance(value, ast.Attribute)
    assert value.attr == "StationCatalogCache"
    assert isinstance(value.value, ast.Name)
    assert value.value.id == "timetable_management_models"


@pytest.mark.parametrize(
    ("relative_path", "canonical_module", "canonical_level"),
    [
        ("rail_waitlist/timetable_evidence.py", "timetable_management.models", 1),
        ("rail_waitlist/watch_management/create_application.py", "timetable_management.models", 2),
    ],
)
def test_timetable_seat_evidence_consumers_use_canonical_model_owner(
    relative_path: str,
    canonical_module: str,
    canonical_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "TimetableSeatEvidence" for alias in node.names)
    }

    assert owner_imports == {(canonical_module, canonical_level)}


def test_all_production_timetable_seat_evidence_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "timetable_management", "models")
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        if module_parts[-1] == "__init__":
            package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not any(
                alias.name == "TimetableSeatEvidence" for alias in node.names
            ):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )

    assert violations == []


def test_central_timetable_seat_evidence_is_an_exact_alias_without_redeclaration() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert "TimetableSeatEvidence" not in class_names
    value = aliases["TimetableSeatEvidence"]
    assert isinstance(value, ast.Attribute)
    assert value.attr == "TimetableSeatEvidence"
    assert isinstance(value.value, ast.Name)
    assert value.value.id == "timetable_management_models"


def test_korail_reservation_driver_uses_canonical_control_policy() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "pydoll" / "reservation_driver.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "booking_seat_control_key" for alias in node.names)
    }

    assert owner_imports == {("provider_adapters.korail_reservation_controls", 3)}


def test_all_production_korail_reservation_control_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "provider_adapters", "korail_reservation_controls")
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        if module_parts[-1] == "__init__":
            package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not any(
                alias.name == "booking_seat_control_key" for alias in node.names
            ):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )

    assert violations == []


def _imported_module_targets(
    source: str,
    *,
    package_parts: tuple[str, ...],
) -> set[tuple[str, ...]]:
    targets: set[tuple[str, ...]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.update(tuple(alias.name.split(".")) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        module_parts = tuple(part for part in (node.module or "").split(".") if part)
        if node.level == 0:
            resolved = module_parts
        else:
            keep = len(package_parts) - (node.level - 1)
            resolved = (*package_parts[:keep], *module_parts)
        targets.add(resolved)
        targets.update(
            (*resolved, *alias.name.split(".")) for alias in node.names if alias.name != "*"
        )
    return targets


def test_all_production_tago_response_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "provider_adapters", "tago_response")
    public_symbols = {"TagoPage", "response_page"}
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not public_symbols.intersection(
                alias.name for alias in node.names
            ):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )

    assert violations == []


def test_tago_runtime_reexports_exact_parser_aliases_without_redeclaration() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_adapters" / "tago.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    parser_definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in {"TagoPage", "response_page"}
    }
    parser_imports = {
        (alias.name, alias.asname, node.module, node.level)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "tago_response"
        for alias in node.names
    }

    assert parser_definitions == set()
    assert parser_imports == {
        ("TagoPage", "TagoPage", "tago_response", 1),
        ("response_page", "response_page", "tago_response", 1),
    }


@pytest.mark.parametrize(
    "source",
    [
        "import rail_waitlist.korail_reservation_controls\n",
        "import rail_waitlist.korail_reservation_controls as controls\n",
        ("from rail_waitlist.korail_reservation_controls import booking_seat_control_key\n"),
        "from .korail_reservation_controls import booking_seat_control_key\n",
        "from rail_waitlist import korail_reservation_controls\n",
        "from . import korail_reservation_controls\n",
    ],
)
def test_legacy_korail_reservation_control_import_detector_covers_all_forms(
    source: str,
) -> None:
    assert ("rail_waitlist", "korail_reservation_controls") in _imported_module_targets(
        source,
        package_parts=("rail_waitlist",),
    )


def test_production_does_not_import_the_legacy_korail_reservation_control_facade() -> None:
    legacy_module = ("rail_waitlist", "korail_reservation_controls")
    facade_path = SOURCE_ROOT / "rail_waitlist" / "korail_reservation_controls.py"
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        if module_path == facade_path:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = tuple(module_parts[:-1])
        source = module_path.read_text(encoding="utf-8")
        if legacy_module in _imported_module_targets(source, package_parts=package_parts):
            violations.append(relative_path.as_posix())

    assert violations == []


def test_top_level_korail_reservation_controls_is_an_exact_alias_facade() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_reservation_controls.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imports = {
        (alias.name, alias.asname, node.module, node.level)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == set()
    assert imports == {
        (
            "booking_seat_control_key",
            "booking_seat_control_key",
            "provider_adapters.korail_reservation_controls",
            1,
        )
    }


@pytest.mark.parametrize(
    "source",
    [
        "import rail_waitlist.korail_execution\n",
        "import rail_waitlist.korail_execution as execution\n",
        "from rail_waitlist.korail_execution import KorailSeatObserver\n",
        "from .korail_execution import KorailSeatObserver\n",
        "from rail_waitlist import korail_execution\n",
        "from . import korail_execution\n",
    ],
)
def test_legacy_korail_execution_import_detector_covers_all_forms(source: str) -> None:
    assert ("rail_waitlist", "korail_execution") in _imported_module_targets(
        source,
        package_parts=("rail_waitlist",),
    )


def test_production_does_not_import_the_legacy_korail_execution_facade() -> None:
    legacy_module = ("rail_waitlist", "korail_execution")
    facade_path = SOURCE_ROOT / "rail_waitlist" / "korail_execution.py"
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        if module_path == facade_path:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = tuple(module_parts[:-1])
        source = module_path.read_text(encoding="utf-8")
        if legacy_module in _imported_module_targets(source, package_parts=package_parts):
            violations.append(relative_path.as_posix())

    assert violations == []


def test_top_level_korail_execution_is_an_exact_alias_facade() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_execution.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imports = {
        (alias.name, alias.asname, node.module, node.level)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == set()
    assert imports == {
        (
            "KorailExecutionSourceConfig",
            "KorailExecutionSourceConfig",
            "provider_adapters.korail_execution",
            1,
        ),
        (
            "KorailSeatObserver",
            "KorailSeatObserver",
            "provider_adapters.korail_execution",
            1,
        ),
        (
            "ManagedKorailSeatObserver",
            "ManagedKorailSeatObserver",
            "provider_adapters.korail_execution",
            1,
        ),
        (
            "default_korail_execution_source",
            "default_korail_execution_source",
            "provider_adapters.korail_execution",
            1,
        ),
        (
            "korail_background_monitoring_enabled",
            "korail_background_monitoring_enabled",
            "provider_adapters.korail_execution",
            1,
        ),
    }


@pytest.mark.parametrize(
    "source",
    [
        "import rail_waitlist.srt_execution\n",
        "import rail_waitlist.srt_execution as execution\n",
        "from rail_waitlist.srt_execution import SrtSeatObserver\n",
        "from .srt_execution import SrtSeatObserver\n",
        "from rail_waitlist import srt_execution\n",
        "from . import srt_execution\n",
    ],
)
def test_legacy_srt_execution_import_detector_covers_all_forms(source: str) -> None:
    assert ("rail_waitlist", "srt_execution") in _imported_module_targets(
        source,
        package_parts=("rail_waitlist",),
    )


def test_production_does_not_import_the_legacy_srt_execution_facade() -> None:
    legacy_module = ("rail_waitlist", "srt_execution")
    facade_path = SOURCE_ROOT / "rail_waitlist" / "srt_execution.py"
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        if module_path == facade_path:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = tuple(module_parts[:-1])
        source = module_path.read_text(encoding="utf-8")
        if legacy_module in _imported_module_targets(source, package_parts=package_parts):
            violations.append(relative_path.as_posix())

    assert violations == []


def test_top_level_srt_execution_is_an_exact_alias_facade() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "srt_execution.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imports = {
        (alias.name, alias.asname, node.module, node.level)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == set()
    assert imports == {
        (
            "ManagedSrtSeatObserver",
            "ManagedSrtSeatObserver",
            "provider_adapters.srt_source_runtime",
            1,
        ),
        (
            "SrtExecutionSourceConfig",
            "SrtExecutionSourceConfig",
            "provider_adapters.srt_source_runtime",
            1,
        ),
        (
            "SrtSeatObserver",
            "SrtSeatObserver",
            "provider_adapters.srt_source_runtime",
            1,
        ),
        (
            "default_srt_execution_source",
            "default_srt_execution_source",
            "provider_adapters.srt_source_runtime",
            1,
        ),
        (
            "srt_background_monitoring_enabled",
            "srt_background_monitoring_enabled",
            "provider_adapters.srt_source_runtime",
            1,
        ),
    }


def test_all_production_srt_station_roster_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "provider_adapters", "srt_station_roster")
    public_symbols = {
        "ROSTER_SOURCE",
        "SrtStationRoster",
        "SrtStationRosterUnavailable",
        "load_srt_station_roster",
        "normalize_srt_station_name",
    }
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not public_symbols.intersection(
                alias.name for alias in node.names
            ):
                continue
            if node.level == 0:
                resolved = tuple(part for part in (node.module or "").split(".") if part)
            else:
                keep = len(package_parts) - (node.level - 1)
                relative_module = tuple(part for part in (node.module or "").split(".") if part)
                resolved = (*package_parts[:keep], *relative_module)
            if resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )

    assert violations == []


@pytest.mark.parametrize(
    "source",
    [
        "import rail_waitlist.srt_station_roster\n",
        "import rail_waitlist.srt_station_roster as roster\n",
        "from rail_waitlist.srt_station_roster import SrtStationRoster\n",
        "from .srt_station_roster import SrtStationRoster\n",
        "from rail_waitlist import srt_station_roster\n",
        "from . import srt_station_roster\n",
    ],
)
def test_legacy_srt_station_roster_import_detector_covers_all_forms(source: str) -> None:
    assert ("rail_waitlist", "srt_station_roster") in _imported_module_targets(
        source,
        package_parts=("rail_waitlist",),
    )


def test_production_does_not_import_the_legacy_srt_station_roster_facade() -> None:
    legacy_module = ("rail_waitlist", "srt_station_roster")
    facade_path = SOURCE_ROOT / "rail_waitlist" / "srt_station_roster.py"
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        if module_path == facade_path:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = tuple(module_parts[:-1])
        source = module_path.read_text(encoding="utf-8")
        if legacy_module in _imported_module_targets(source, package_parts=package_parts):
            violations.append(relative_path.as_posix())

    assert violations == []


def test_top_level_srt_station_roster_is_an_exact_alias_facade() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "srt_station_roster.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imports = {
        (alias.name, alias.asname, node.module, node.level)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == set()
    assert imports == {
        ("ROSTER_SOURCE", "ROSTER_SOURCE", "provider_adapters.srt_station_roster", 1),
        (
            "SrtStationRoster",
            "SrtStationRoster",
            "provider_adapters.srt_station_roster",
            1,
        ),
        (
            "SrtStationRosterUnavailable",
            "SrtStationRosterUnavailable",
            "provider_adapters.srt_station_roster",
            1,
        ),
        (
            "load_srt_station_roster",
            "load_srt_station_roster",
            "provider_adapters.srt_station_roster",
            1,
        ),
        (
            "normalize_srt_station_name",
            "normalize_srt_station_name",
            "provider_adapters.srt_station_roster",
            1,
        ),
    }


def test_timetable_application_uses_canonical_srt_live_projection_owner() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "timetable_management" / "application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "map_srt_live_timetable" for alias in node.names)
    }

    assert owner_imports == {("srt_live_timetable", 1)}


def test_all_production_srt_live_projection_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "timetable_management", "srt_live_timetable")
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        if module_parts[-1] == "__init__":
            package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not any(
                alias.name == "map_srt_live_timetable" for alias in node.names
            ):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )

    assert violations == []


def test_production_does_not_import_the_legacy_srt_live_timetable_facade() -> None:
    legacy_module = ("rail_waitlist", "srt_live_timetable")
    facade_path = SOURCE_ROOT / "rail_waitlist" / "srt_live_timetable.py"
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        if module_path == facade_path:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = tuple(module_parts[:-1])
        source = module_path.read_text(encoding="utf-8")
        if legacy_module in _imported_module_targets(source, package_parts=package_parts):
            violations.append(relative_path.as_posix())

    assert violations == []


def test_top_level_srt_live_timetable_is_an_exact_alias_facade() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "srt_live_timetable.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    function_names = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (alias.name, alias.asname, node.module, node.level)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert function_names == set()
    assert imports == {
        (
            "_seat_class",
            "_seat_class",
            "timetable_management.srt_live_timetable",
            1,
        ),
        (
            "map_srt_live_timetable",
            "map_srt_live_timetable",
            "timetable_management.srt_live_timetable",
            1,
        ),
    }


def test_central_station_catalog_schemas_are_exact_aliases_without_redeclaration() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    for symbol in ("StationItem", "StationCatalog"):
        assert symbol not in class_names
        value = aliases[symbol]
        assert isinstance(value, ast.Attribute)
        assert value.attr == symbol
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "timetable_management_schemas"


def test_central_seat_status_refresh_schema_is_an_exact_alias_without_redeclaration() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert "SeatStatusRefreshRequest" not in class_names
    value = aliases["SeatStatusRefreshRequest"]
    assert isinstance(value, ast.Attribute)
    assert value.attr == "SeatStatusRefreshRequest"
    assert isinstance(value.value, ast.Name)
    assert value.value.id == "timetable_management_schemas"


def test_central_seat_status_source_schemas_are_exact_aliases_without_redeclaration() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    for symbol in ("SeatStatusCooldownCause", "SeatStatusSourceStatus"):
        assert symbol not in class_names
        value = aliases[symbol]
        assert isinstance(value, ast.Attribute)
        assert value.attr == symbol
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "seat_status_operations_schemas"


@pytest.mark.parametrize(
    "symbol",
    [
        "RegistrationEvidenceConflictDetail",
        "WatchCandidateCreate",
        "WatchCreate",
        "WatchUpdate",
        "WatchCandidateLatestReservationAttemptRead",
        "WatchCandidateRead",
        "WatchCandidateLatestObservationRead",
        "WatchRead",
    ],
)
def test_central_watch_schema_is_an_exact_alias_without_redeclaration(symbol: str) -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert symbol not in class_names
    value = aliases[symbol]
    assert isinstance(value, ast.Attribute)
    assert value.attr == symbol
    assert isinstance(value.value, ast.Name)
    assert value.value.id == "watch_management_schemas"


@pytest.mark.parametrize(
    "symbol",
    ["RegistrationEvidenceConflictDetail", "WatchCreate", "WatchUpdate"],
)
def test_services_imports_the_feature_local_watch_schema(symbol: str) -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "services.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert imports == {("watch_management.schemas", 1)}


@pytest.mark.parametrize(
    "symbol",
    [
        "RegistrationEvidenceConflictDetail",
        "WatchCandidateCreate",
        "WatchCreate",
        "WatchUpdate",
        "WatchCandidateLatestReservationAttemptRead",
        "WatchCandidateRead",
        "WatchCandidateLatestObservationRead",
        "WatchRead",
    ],
)
def test_all_production_watch_schema_imports_resolve_to_canonical_owner(symbol: str) -> None:
    canonical_owner = ("rail_waitlist", "watch_management", "schemas")
    legacy_owner = ("rail_waitlist", "schemas")
    violations: list[str] = []

    def attribute_path(node: ast.AST) -> str | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        return ".".join(reversed(parts))

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        legacy_bindings: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if tuple(alias.name.split(".")) == legacy_owner:
                        legacy_bindings.add(alias.asname or alias.name)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if any(alias.name == symbol for alias in node.names) and resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )
            if resolved == ("rail_waitlist",):
                legacy_bindings.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "schemas"
                )

        legacy_paths = {f"{binding}.{symbol}" for binding in legacy_bindings} | {
            f"rail_waitlist.schemas.{symbol}"
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and attribute_path(node) in legacy_paths:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> legacy module")

    assert violations == []


@pytest.mark.parametrize(
    ("relative_path", "symbol", "expected_module", "expected_level"),
    [
        ("rail_waitlist/watch_management/create_application.py", "WatchCreate", "schemas", 1),
        ("rail_waitlist/watch_management/command_runtime.py", "WatchCreate", "schemas", 1),
        ("rail_waitlist/watch_management/command_runtime.py", "WatchUpdate", "schemas", 1),
        ("rail_waitlist/watch_management/http.py", "WatchCreate", "schemas", 1),
        ("rail_waitlist/watch_management/http.py", "WatchRead", "schemas", 1),
        ("rail_waitlist/watch_management/http.py", "WatchUpdate", "schemas", 1),
        ("rail_waitlist/watch_management/read_model.py", "WatchRead", "schemas", 1),
        ("rail_waitlist/watch_management/update_application.py", "WatchUpdate", "schemas", 1),
    ],
)
def test_watch_schema_consumers_import_the_feature_local_schema(
    relative_path: str,
    symbol: str,
    expected_module: str,
    expected_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert imports == {(expected_module, expected_level)}


def test_korail_confirmation_facade_is_an_exact_canonical_export_surface() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_reservation_confirmation.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols = {
        "KORAIL_CONFIRMATION_SOURCE",
        "KORAIL_ISSUED_TICKET_LIST_SOURCE",
        "KORAIL_RESERVATION_HANDOFF_URL",
        "KORAIL_RESERVATION_LIST_SOURCE",
        "KorailSameSessionDetailConfirmationAdapter",
        "KorailSameSessionDetailEvidence",
        "KorailSameSessionDetailProbe",
        "normalize_korail_same_session_detail",
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    all_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )

    assert definitions == []
    assert imports == {
        ("reservations.provider_confirmation.korail", 1, symbol, symbol) for symbol in symbols
    }
    assert isinstance(all_assignment.value, ast.Tuple)
    assert {
        item.value for item in all_assignment.value.elts if isinstance(item, ast.Constant)
    } == symbols


def test_korail_confirmation_owner_has_an_exact_import_allowlist() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "reservations" / "provider_confirmation" / "korail.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = [node for node in tree.body if isinstance(node, ast.Import)]
    imports_from = {
        (node.module, node.level) for node in tree.body if isinstance(node, ast.ImportFrom)
    }

    assert direct_imports == []
    assert imports_from == {
        ("__future__", 0),
        ("dataclasses", 0),
        ("datetime", 0),
        ("typing", 0),
        ("domain", 3),
        ("provider_registry.official_url_policy", 3),
        ("contracts", 1),
    }


def test_worker_task_runtime_has_an_exact_import_allowlist() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker_task_runtime.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import)]
    imports_from = {
        (node.module, node.level) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert direct_imports == []
    assert imports_from == {
        ("__future__", 0),
        ("collections.abc", 0),
    }


def test_srt_identity_owner_has_an_exact_import_allowlist() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_adapters" / "srt_identity.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import)]
    imports_from = {
        (node.module, node.level) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert direct_imports == []
    assert imports_from == {("__future__", 0)}


def test_srt_identity_consumers_import_the_canonical_owner() -> None:
    symbols = {
        "normalize_srt_train_number",
        "normalize_srt_date",
        "normalize_srt_time",
    }
    expectations = {
        "rail_waitlist/provider_adapters/srt_seat_source.py": (
            "srt_identity",
            1,
            {(symbol, symbol) for symbol in symbols},
        ),
        "rail_waitlist/srt_sidecar/reservation.py": (
            "provider_adapters.srt_identity",
            2,
            {(symbol, None) for symbol in symbols},
        ),
        "rail_waitlist/reservations/provider_confirmation/srt.py": (
            "provider_adapters.srt_identity",
            3,
            {(symbol, None) for symbol in symbols},
        ),
    }

    for relative_path, (expected_module, expected_level, expected_imports) in expectations.items():
        module_path = SOURCE_ROOT / relative_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in symbols
        }
        imports = {
            (alias.name, alias.asname)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == expected_module
            and node.level == expected_level
            for alias in node.names
        }

        assert definitions == set()
        assert imports == expected_imports


def _legacy_srt_identity_imports(source: str, relative_path: Path) -> list[str]:
    symbols = {
        "normalize_srt_train_number",
        "normalize_srt_date",
        "normalize_srt_time",
    }
    violations: list[str] = []
    legacy_owner = ("rail_waitlist", "srt_seat_source")
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if tuple(alias.name.split(".")) == legacy_owner:
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_module_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_module_parts)

        if resolved == legacy_owner:
            imported = symbols.intersection(alias.name for alias in node.names)
            wildcard = any(alias.name == "*" for alias in node.names)
            if imported or wildcard:
                detail = "*" if wildcard else ",".join(sorted(imported))
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols={detail}")
        if resolved == ("rail_waitlist",) and any(
            alias.name == "srt_seat_source" for alias in node.names
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module-style")

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from .srt_seat_source import normalize_srt_date",
        "from rail_waitlist.srt_seat_source import normalize_srt_date",
        "import rail_waitlist.srt_seat_source",
        "import rail_waitlist.srt_seat_source as source",
        "from rail_waitlist import srt_seat_source",
        "from . import srt_seat_source",
        "from .srt_seat_source import *",
    ],
)
def test_srt_identity_legacy_import_detector_rejects_all_access_forms(source: str) -> None:
    assert _legacy_srt_identity_imports(source, Path("rail_waitlist/probe.py"))


def test_srt_identity_legacy_import_detector_allows_other_direct_symbols() -> None:
    assert (
        _legacy_srt_identity_imports(
            "from .srt_seat_source import SrtLiveSeatSource",
            Path("rail_waitlist/probe.py"),
        )
        == []
    )


def test_production_does_not_import_srt_identity_from_the_legacy_source() -> None:
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == "rail_waitlist/srt_seat_source.py":
            continue
        violations.extend(
            _legacy_srt_identity_imports(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_srt_confirmation_facade_is_an_exact_canonical_export_surface() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "srt_reservation_confirmation.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols = {
        "SRT_RESERVATION_HANDOFF_URL",
        "SRT_RESERVATION_LIST_SOURCE",
        "SRT_RESERVE_RESULT_SOURCE",
        "SrtReadOnlyReservationListProbe",
        "SrtReservationListConfirmationAdapter",
        "SrtReservationListEvidence",
        "SrtReservationRecord",
        "normalize_srt_reservation_records",
        "normalize_srt_reserve_result",
    }
    identity_compatibility_symbols = {
        "normalize_srt_train_number",
        "normalize_srt_date",
        "normalize_srt_time",
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    all_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )

    assert definitions == []
    assert imports == {
        *{("reservations.provider_confirmation.srt", 1, symbol, symbol) for symbol in symbols},
        *{
            ("provider_adapters.srt_identity", 1, symbol, symbol)
            for symbol in identity_compatibility_symbols
        },
    }
    assert isinstance(all_assignment.value, ast.Tuple)
    assert {
        item.value for item in all_assignment.value.elts if isinstance(item, ast.Constant)
    } == symbols


def test_srt_confirmation_owner_has_an_exact_import_allowlist() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "reservations" / "provider_confirmation" / "srt.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import)]
    imports_from = {
        (node.module, node.level) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert direct_imports == []
    assert imports_from == {
        ("__future__", 0),
        ("dataclasses", 0),
        ("datetime", 0),
        ("typing", 0),
        ("zoneinfo", 0),
        ("domain", 3),
        ("provider_adapters.srt_identity", 3),
        ("provider_registry.official_url_policy", 3),
        ("contracts", 1),
    }


def test_srt_reservation_imports_the_canonical_confirmation_owner() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "srt_sidecar" / "reservation.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols = {
        "SRT_RESERVATION_LIST_SOURCE",
        "SrtReservationListEvidence",
        "SrtReservationRecord",
        "normalize_srt_reservation_records",
    }
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "reservations.provider_confirmation.srt"
        and node.level == 2
        for alias in node.names
    }

    assert imports == {(symbol, None) for symbol in symbols}


def _legacy_srt_confirmation_imports(source: str, relative_path: Path) -> list[str]:
    legacy_owner = ("rail_waitlist", "srt_reservation_confirmation")
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if tuple(alias.name.split(".")) == legacy_owner:
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_module_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_module_parts)
        if resolved == legacy_owner:
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == ("rail_waitlist",) and any(
            alias.name == "srt_reservation_confirmation" for alias in node.names
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module-style")

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from .srt_reservation_confirmation import SrtReservationRecord",
        "from rail_waitlist.srt_reservation_confirmation import SrtReservationRecord",
        "import rail_waitlist.srt_reservation_confirmation",
        "import rail_waitlist.srt_reservation_confirmation as confirmation",
        "from rail_waitlist import srt_reservation_confirmation",
        "from . import srt_reservation_confirmation",
        "from .srt_reservation_confirmation import *",
    ],
)
def test_srt_confirmation_legacy_import_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _legacy_srt_confirmation_imports(source, Path("rail_waitlist/probe.py"))


def test_production_does_not_reenter_the_legacy_srt_confirmation_facade() -> None:
    violations: list[str] = []
    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == "rail_waitlist/srt_reservation_confirmation.py":
            continue
        violations.extend(
            _legacy_srt_confirmation_imports(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_outbox_model_owner_has_an_exact_import_allowlist() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "outbox_management" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert direct_imports == {("uuid", None)}
    assert imports_from == {
        ("__future__", 0),
        ("datetime", 0),
        ("typing", 0),
        ("sqlalchemy", 0),
        ("sqlalchemy.orm", 0),
        ("database", 2),
        ("domain", 2),
    }


def test_central_models_is_an_exact_outbox_model_alias() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OutboxEvent"
    ]
    owner_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "outbox_management"
        for alias in node.names
    }
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "OutboxEvent" for target in node.targets
        )
    ]

    assert definitions == []
    assert owner_imports == {("outbox_management", 1, "models", "outbox_management_models")}
    assert len(assignments) == 1
    value = assignments[0].value
    assert isinstance(value, ast.Attribute)
    assert isinstance(value.value, ast.Name)
    assert (value.value.id, value.attr) == ("outbox_management_models", "OutboxEvent")


@pytest.mark.parametrize(
    ("relative_path", "expected_level"),
    [
        ("rail_waitlist/outbox.py", 1),
        ("rail_waitlist/event_stream/http.py", 2),
        ("rail_waitlist/notification_management/delivery.py", 2),
        ("rail_waitlist/main.py", 1),
        ("rail_waitlist/operations.py", 1),
        ("rail_waitlist/reservations/execution_application.py", 2),
    ],
)
def test_outbox_model_consumers_import_the_canonical_owner(
    relative_path: str,
    expected_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "OutboxEvent" for alias in node.names)
        for alias in node.names
        if alias.name == "OutboxEvent"
    }

    assert imports == {("outbox_management.models", expected_level, "OutboxEvent", None)}


def test_production_outbox_model_references_use_direct_canonical_imports() -> None:
    canonical_owner = ("rail_waitlist", "outbox_management", "models")
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in {
            "rail_waitlist/models.py",
            "rail_waitlist/outbox_management/models.py",
        }:
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "OutboxEvent":
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> module-style attribute"
                )
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                imported_module_parts = tuple(
                    part for part in (node.module or "").split(".") if part
                )
                resolved = (*package_parts[:keep], *imported_module_parts)
            if any(alias.name == "OutboxEvent" for alias in node.names):
                if resolved != canonical_owner:
                    violations.append(
                        f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                    )
            if resolved == ("rail_waitlist", "models") and any(
                alias.name == "*" for alias in node.names
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> central wildcard")

    assert violations == []


def test_worker_task_cleanup_wrapper_only_injects_the_current_engine_disposer() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owner_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "worker_task_runtime"
        for alias in node.names
    }
    wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_isolated"
    )
    calls = [
        node
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_task_isolated"
    ]

    assert owner_imports == {("worker_task_runtime", 1, "run_task_isolated", None)}
    assert not any(isinstance(node, ast.Try) for node in ast.walk(wrapper))
    assert len(calls) == 1
    assert len(calls[0].args) == 1
    assert isinstance(calls[0].args[0], ast.Name)
    assert calls[0].args[0].id == "operation"
    assert len(calls[0].keywords) == 1
    assert calls[0].keywords[0].arg == "dispose_engine"
    disposer = calls[0].keywords[0].value
    assert isinstance(disposer, ast.Attribute)
    assert isinstance(disposer.value, ast.Name)
    assert (disposer.value.id, disposer.attr) == ("engine", "dispose")


def test_worker_celery_tasks_keep_the_isolated_cleanup_wrapper() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    task_names = {
        "process_due_watches": "rail_waitlist.worker.process_due_watches",
        "process_watch_now": "rail_waitlist.worker.process_watch_now",
        "reconcile_reservation_attempt": "rail_waitlist.worker.reconcile_reservation_attempt",
        "deliver_outbox": "rail_waitlist.worker.deliver_outbox",
    }
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in task_names
    }

    assert set(functions) == set(task_names)
    for function_name, function in functions.items():
        asyncio_run_calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and (node.func.value.id, node.func.attr) == ("asyncio", "run")
        ]
        assert len(asyncio_run_calls) == 1
        assert len(asyncio_run_calls[0].args) == 1
        isolated_call = asyncio_run_calls[0].args[0]
        assert isinstance(isolated_call, ast.Call)
        assert isinstance(isolated_call.func, ast.Name)
        assert isolated_call.func.id == "_run_isolated"
        assert len(isolated_call.args) == 1
        assert isolated_call.keywords == []

        assert len(function.decorator_list) == 1
        decorator = function.decorator_list[0]
        assert isinstance(decorator, ast.Call)
        assert isinstance(decorator.func, ast.Attribute)
        assert isinstance(decorator.func.value, ast.Name)
        assert (decorator.func.value.id, decorator.func.attr) == ("celery_app", "task")
        assert decorator.args == []
        assert len(decorator.keywords) == 1
        assert decorator.keywords[0].arg == "name"
        assert isinstance(decorator.keywords[0].value, ast.Constant)
        assert decorator.keywords[0].value.value == task_names[function_name]


@pytest.mark.parametrize(
    ("relative_path", "expected_level", "expected_symbols"),
    [
        (
            "rail_waitlist/korail_pydoll_browser.py",
            1,
            {"KorailSameSessionDetailEvidence"},
        ),
        (
            "rail_waitlist/korail_sidecar/pydoll/confirmation_reader.py",
            3,
            {
                "KORAIL_CONFIRMATION_SOURCE",
                "KORAIL_ISSUED_TICKET_LIST_SOURCE",
                "KORAIL_RESERVATION_LIST_SOURCE",
                "KorailSameSessionDetailEvidence",
            },
        ),
        (
            "rail_waitlist/korail_sidecar/http.py",
            2,
            {"KORAIL_CONFIRMATION_SOURCE", "normalize_korail_same_session_detail"},
        ),
    ],
)
def test_korail_confirmation_consumers_import_the_canonical_owner(
    relative_path: str,
    expected_level: int,
    expected_symbols: set[str],
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "reservations.provider_confirmation.korail"
        and node.level == expected_level
        for alias in node.names
    }

    assert imports == {(symbol, None) for symbol in expected_symbols}


def test_production_does_not_reenter_the_legacy_korail_confirmation_facade() -> None:
    legacy_owner = ("rail_waitlist", "korail_reservation_confirmation")
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == "rail_waitlist/korail_reservation_confirmation.py":
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if tuple(alias.name.split(".")) == legacy_owner:
                        violations.append(
                            f"{relative_path.as_posix()}:{node.lineno} -> {alias.name}"
                        )
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if resolved == legacy_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )
            if resolved == ("rail_waitlist",) and any(
                alias.name == "korail_reservation_confirmation" for alias in node.names
            ):
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> module-style facade"
                )

    assert violations == []


def test_seat_status_http_imports_its_feature_local_source_schema() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "seat_status_operations" / "http.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "SeatStatusSourceStatus" for alias in node.names)
    }

    assert imports == {("schemas", 1)}


def test_all_production_seat_status_source_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "seat_status_operations", "schemas")
    legacy_owner = ("rail_waitlist", "schemas")
    symbols = {"SeatStatusCooldownCause", "SeatStatusSourceStatus"}
    violations: list[str] = []

    def attribute_path(node: ast.AST) -> str | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        return ".".join(reversed(parts))

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        legacy_bindings: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if tuple(alias.name.split(".")) == legacy_owner:
                        legacy_bindings.add(alias.asname or alias.name)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if symbols.intersection(alias.name for alias in node.names):
                if resolved != canonical_owner:
                    violations.append(
                        f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                    )
            if resolved == ("rail_waitlist",):
                legacy_bindings.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "schemas"
                )

        legacy_paths = {
            f"{binding}.{symbol}" for binding in legacy_bindings for symbol in symbols
        } | {f"rail_waitlist.schemas.{symbol}" for symbol in symbols}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and attribute_path(node) in legacy_paths:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> legacy module")

    assert violations == []


def test_all_production_seat_status_refresh_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "timetable_management", "schemas")
    legacy_owner = ("rail_waitlist", "schemas")
    violations: list[str] = []

    def attribute_path(node: ast.AST) -> str | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        return ".".join(reversed(parts))

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        legacy_bindings: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if tuple(alias.name.split(".")) == legacy_owner:
                        legacy_bindings.add(alias.asname or alias.name)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if any(alias.name == "SeatStatusRefreshRequest" for alias in node.names):
                if resolved != canonical_owner:
                    violations.append(
                        f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                    )
            if resolved == ("rail_waitlist",):
                legacy_bindings.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "schemas"
                )

        legacy_paths = {f"{binding}.SeatStatusRefreshRequest" for binding in legacy_bindings} | {
            "rail_waitlist.schemas.SeatStatusRefreshRequest"
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and attribute_path(node) in legacy_paths:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> legacy module")

    assert violations == []


def test_all_production_station_schema_imports_resolve_to_canonical_owner() -> None:
    canonical_owner = ("rail_waitlist", "timetable_management", "schemas")
    station_symbols = {"StationItem", "StationCatalog"}
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        if module_parts[-1] == "__init__":
            package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not station_symbols.intersection(
                alias.name for alias in node.names
            ):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                resolved = (*package_parts[:keep], *((node.module or "").split(".")))
            if resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )

    assert violations == []


def test_provider_facade_reexports_the_canonical_station_catalog_schema() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "providers.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    station_catalog_imports = {
        (node.module, node.level, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "StationCatalog"
    }

    assert station_catalog_imports == {("timetable_management.schemas", 1, "StationCatalog")}


def test_station_catalog_facade_only_reexports_the_canonical_application() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "station_catalog_cache.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules == {("timetable_management.catalog_application", 1)}


def test_main_composes_the_canonical_station_catalog_service() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "main.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    service_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "StationCatalogService" for alias in node.names)
    }

    assert service_imports == {("timetable_management.catalog_application", 1)}


def test_station_visibility_facade_only_reexports_the_canonical_policy() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "station_visibility.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules == {("timetable_management.station_visibility", 1)}


def test_station_catalog_application_uses_feature_local_visibility_policy() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "timetable_management" / "catalog_application.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    visibility_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "KorailStationVisibility" for alias in node.names)
    }

    assert visibility_imports == {("station_visibility", 1)}


def test_main_composes_the_canonical_station_visibility_policy() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "main.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    visibility_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "KorailStationVisibility" for alias in node.names)
    }

    assert visibility_imports == {("timetable_management.station_visibility", 1)}


def test_production_does_not_import_the_legacy_station_visibility_facade() -> None:
    legacy_module = ("rail_waitlist", "station_visibility")
    facade_path = SOURCE_ROOT / "rail_waitlist" / "station_visibility.py"
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        if module_path == facade_path:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT)
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = tuple(module_parts[:-1])
        source = module_path.read_text(encoding="utf-8")
        if legacy_module in _imported_module_targets(source, package_parts=package_parts):
            violations.append(relative_path.as_posix())

    assert violations == []


@pytest.mark.parametrize(
    ("relative_path", "symbol"),
    [
        ("rail_waitlist/timetable_management/application.py", "TimetableApplication"),
        ("rail_waitlist/timetable_management/catalog_http.py", "StationCatalogReader"),
        ("rail_waitlist/timetable_management/http.py", "TimetableApplication"),
    ],
)
def test_timetable_consumers_use_the_feature_contract_owner(
    relative_path: str,
    symbol: str,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    contract_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names)
    }

    assert contract_imports == {("contracts", 1)}


def test_timetable_contracts_do_not_import_the_catalog_implementation() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "timetable_management" / "contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "catalog_application" not in imported_modules


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


def test_korail_sidecar_contract_owner_has_an_exact_import_allowlist() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import)]
    imports_from = {
        (node.module, node.level) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert direct_imports == []
    assert imports_from == {
        ("__future__", 0),
        ("datetime", 0),
        ("typing", 0),
        ("pydantic", 0),
    }


def test_korail_reservation_contract_facade_exactly_aliases_sidecar_contracts() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_reservation_contract.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols = {
        "BaseModel",
        "ConfigDict",
        "Field",
        "KorailCredentialRequest",
        "KorailConfirmationPurposeValue",
        "KorailLoginMethodValue",
        "KorailLoginVerificationOutcomeValue",
        "KorailLoginVerifyRequest",
        "KorailLoginVerifyResult",
        "KorailReservationConfirmationRequest",
        "KorailReservationConfirmationResult",
        "KorailReservationOutcomeValue",
        "KorailReservationSeatClassValue",
        "KorailReservedSeat",
        "KorailReserveOnceRequest",
        "KorailReserveOnceResult",
        "KorailSessionActorStateValue",
        "KorailSessionStateResult",
        "Literal",
        "SecretStr",
        "_InternalModel",
        "clock_time",
        "date",
        "datetime",
        "field_validator",
        "model_validator",
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    owner_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assignments = {
        node.targets[0].id: node.value.attr
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "_contracts"
    }

    assert definitions == []
    assert owner_imports == {("korail_sidecar", 1, "contracts", "_contracts")}
    assert assignments == {symbol: symbol for symbol in symbols}


@pytest.mark.parametrize(
    ("relative_path", "expected_module", "expected_level", "expected_imports"),
    [
        (
            "rail_waitlist/korail_browser_seat_source.py",
            "korail_sidecar.contracts",
            1,
            {
                ("KorailCredentialRequest", "KorailCredentialRequest"),
                ("KorailLoginVerifyRequest", "KorailLoginVerifyRequest"),
                ("KorailLoginVerifyResult", "KorailLoginVerifyResult"),
                (
                    "KorailReservationConfirmationRequest",
                    "KorailReservationConfirmationRequest",
                ),
                (
                    "KorailReservationConfirmationResult",
                    "KorailReservationConfirmationResult",
                ),
                ("KorailReserveOnceRequest", "KorailReserveOnceRequest"),
                ("KorailReserveOnceResult", "KorailReserveOnceResult"),
                ("KorailSessionStateResult", None),
            },
        ),
        (
            "rail_waitlist/provider_adapters/korail_browser_auth_policy.py",
            "korail_sidecar.contracts",
            2,
            {
                ("KorailCredentialRequest", None),
                ("KorailLoginVerifyRequest", None),
                ("KorailLoginVerifyResult", None),
            },
        ),
        (
            "rail_waitlist/korail_sidecar/http.py",
            "contracts",
            1,
            {
                ("KorailLoginVerificationOutcomeValue", None),
                ("KorailLoginVerifyRequest", None),
                ("KorailLoginVerifyResult", None),
                ("KorailReservationConfirmationRequest", None),
                ("KorailReservationConfirmationResult", None),
                ("KorailReservationOutcomeValue", None),
                ("KorailReservedSeat", None),
                ("KorailReserveOnceRequest", None),
                ("KorailReserveOnceResult", None),
                ("KorailReserveProgressFrame", None),
                ("KorailReserveResultFrame", None),
                ("KorailSessionActorStateValue", None),
                ("KorailSessionStateResult", None),
            },
        ),
        (
            "rail_waitlist/provider_account_management/login_verification.py",
            "korail_sidecar.contracts",
            2,
            {("KorailSessionStateResult", None)},
        ),
    ],
)
def test_korail_sidecar_contract_consumers_import_the_canonical_owner(
    relative_path: str,
    expected_module: str,
    expected_level: int,
    expected_imports: set[tuple[str, str | None]],
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == expected_module
        and node.level == expected_level
        for alias in node.names
    }

    assert imports == expected_imports


def _legacy_korail_reservation_contract_imports(
    source: str,
    relative_path: Path,
) -> list[str]:
    legacy_owner = ("rail_waitlist", "korail_reservation_contract")
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if tuple(alias.name.split(".")) == legacy_owner:
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_module_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_module_parts)
        if resolved == legacy_owner:
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == ("rail_waitlist",) and any(
            alias.name == "korail_reservation_contract" for alias in node.names
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module-style")

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from .korail_reservation_contract import KorailCredentialRequest",
        "from rail_waitlist.korail_reservation_contract import KorailCredentialRequest",
        "import rail_waitlist.korail_reservation_contract",
        "import rail_waitlist.korail_reservation_contract as contracts",
        "from rail_waitlist import korail_reservation_contract",
        "from . import korail_reservation_contract",
        "from .korail_reservation_contract import *",
    ],
)
def test_legacy_korail_reservation_contract_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _legacy_korail_reservation_contract_imports(
        source,
        Path("rail_waitlist/probe.py"),
    )


def test_production_does_not_reenter_the_legacy_korail_reservation_contract() -> None:
    violations: list[str] = []
    facade_path = "rail_waitlist/korail_reservation_contract.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == facade_path:
            continue
        violations.extend(
            _legacy_korail_reservation_contract_imports(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_watch_model_owner_has_an_exact_import_allowlist() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert direct_imports == {("uuid", None)}
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "UTC", None),
        ("datetime", 0, "date", None),
        ("datetime", 0, "datetime", None),
        ("datetime", 0, "time", None),
        ("sqlalchemy", 0, "JSON", None),
        ("sqlalchemy", 0, "Boolean", None),
        ("sqlalchemy", 0, "CheckConstraint", None),
        ("sqlalchemy", 0, "Date", None),
        ("sqlalchemy", 0, "DateTime", None),
        ("sqlalchemy", 0, "Enum", None),
        ("sqlalchemy", 0, "ForeignKey", None),
        ("sqlalchemy", 0, "Index", None),
        ("sqlalchemy", 0, "Integer", None),
        ("sqlalchemy", 0, "String", None),
        ("sqlalchemy", 0, "Text", None),
        ("sqlalchemy", 0, "Time", None),
        ("sqlalchemy", 0, "UniqueConstraint", None),
        ("sqlalchemy.orm", 0, "Mapped", None),
        ("sqlalchemy.orm", 0, "mapped_column", None),
        ("sqlalchemy.orm", 0, "relationship", None),
        ("database", 2, "Base", None),
        ("domain", 2, "BookingWindowStatus", None),
        ("domain", 2, "OperationalStatus", None),
        ("domain", 2, "Provider", None),
        ("domain", 2, "ReservationOutcome", None),
        ("domain", 2, "ReservationPolicy", None),
        ("domain", 2, "SeatObservationMode", None),
        ("domain", 2, "SeatObservationStatus", None),
        ("domain", 2, "WatchStatus", None),
        (
            "reservations.provider_confirmation.contracts",
            2,
            "ReservationConfirmationOutcome",
            None,
        ),
        ("timetable_management.models", 2, "TimetableSeatEvidence", None),
    }


def test_watch_management_package_stays_a_passive_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert imports == []
    assert definitions == []


def test_central_models_exactly_aliases_the_watch_persistence_graph() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols = {
        "ReservationAttempt",
        "SeatObservation",
        "Watch",
        "WatchCandidate",
        "WatchTransitionHistory",
        "utcnow",
    }
    owner_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "watch_management"
        and node.level == 1
        for alias in node.names
    }
    assignments = {
        node.targets[0].id: node.value.attr
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in symbols
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "watch_management_models"
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert owner_imports == {("watch_management", 1, "models", "watch_management_models")}
    assert assignments == {symbol: symbol for symbol in symbols}
    assert definitions == []


@pytest.mark.parametrize(
    ("relative_path", "expected_module", "expected_level", "expected_symbols"),
    [
        (
            "rail_waitlist/notification_management/watch_transition_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/observations/cycle_application.py",
            "watch_management.models",
            2,
            {"SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/observations/due_pipeline_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/observations/group_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/observations/recording_application.py",
            "watch_management.models",
            2,
            {"SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/operations.py",
            "watch_management.models",
            1,
            {
                "ReservationAttempt",
                "SeatObservation",
                "Watch",
                "WatchCandidate",
                "WatchTransitionHistory",
            },
        ),
        (
            "rail_waitlist/provider_account_management/auth_recovery_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "Watch", "WatchCandidate", "WatchTransitionHistory"},
        ),
        (
            "rail_waitlist/reservations/attempt_claim_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/reservations/attempt_result_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/reservations/execution_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/reservations/payment_hold_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt"},
        ),
        (
            "rail_waitlist/reservations/reconciliation_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/reservations/reconciliation_state_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/reservations/stale_attempt_recovery_application.py",
            "watch_management.models",
            2,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/services.py",
            "watch_management.models",
            1,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/ui_preferences/application.py",
            "watch_management.models",
            2,
            {"Watch"},
        ),
        ("rail_waitlist/watch_management/application.py", "models", 1, {"Watch"}),
        ("rail_waitlist/watch_management/arming_application.py", "models", 1, {"Watch"}),
        (
            "rail_waitlist/watch_management/create_application.py",
            "models",
            1,
            {"Watch", "WatchCandidate"},
        ),
        ("rail_waitlist/watch_management/command_runtime.py", "models", 1, {"Watch"}),
        ("rail_waitlist/watch_management/expiry_application.py", "models", 1, {"Watch"}),
        (
            "rail_waitlist/watch_management/http.py",
            "models",
            1,
            {"ReservationAttempt", "Watch", "WatchCandidate"},
        ),
        ("rail_waitlist/watch_management/lookup_application.py", "models", 1, {"Watch"}),
        (
            "rail_waitlist/watch_management/read_model.py",
            "models",
            1,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
        (
            "rail_waitlist/watch_management/transition_application.py",
            "models",
            1,
            {"SeatObservation", "Watch", "WatchTransitionHistory"},
        ),
        (
            "rail_waitlist/watch_management/transition_command_application.py",
            "models",
            1,
            {"Watch"},
        ),
        (
            "rail_waitlist/watch_management/transition_runtime.py",
            "models",
            1,
            {"SeatObservation", "Watch"},
        ),
        ("rail_waitlist/watch_management/update_application.py", "models", 1, {"Watch"}),
        (
            "rail_waitlist/worker.py",
            "watch_management.models",
            1,
            {"ReservationAttempt", "SeatObservation", "Watch", "WatchCandidate"},
        ),
    ],
)
def test_watch_model_consumers_import_the_canonical_owner(
    relative_path: str,
    expected_module: str,
    expected_level: int,
    expected_symbols: set[str],
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    watch_symbols = {
        "ReservationAttempt",
        "SeatObservation",
        "Watch",
        "WatchCandidate",
        "WatchTransitionHistory",
    }
    imports = {
        (alias.name, None if alias.asname in {None, alias.name} else alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == expected_module
        and node.level == expected_level
        for alias in node.names
        if alias.name in watch_symbols
    }

    assert imports == {(symbol, None) for symbol in expected_symbols}


@pytest.mark.parametrize(
    "relative_path",
    [
        "rail_waitlist/observations/recording_application.py",
        "rail_waitlist/provider_account_management/auth_recovery_application.py",
        "rail_waitlist/reservations/stale_attempt_recovery_application.py",
    ],
)
def test_policy_applications_only_cross_into_watch_management_for_models(
    relative_path: str,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    watch_management_imports = {
        (node.module, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("watch_management")
    }

    assert watch_management_imports == {("watch_management.models", 2)}


def test_production_watch_model_references_use_direct_canonical_imports() -> None:
    canonical_owner = ("rail_waitlist", "watch_management", "models")
    central_owner = ("rail_waitlist", "models")
    watch_symbols = {
        "ReservationAttempt",
        "SeatObservation",
        "Watch",
        "WatchCandidate",
        "WatchTransitionHistory",
    }
    excluded_paths = {
        "rail_waitlist/main.py",
        "rail_waitlist/models.py",
        "rail_waitlist/watch_management/models.py",
    }
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in excluded_paths:
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_owner = tuple(alias.name.split("."))
                    if imported_owner == central_owner:
                        violations.append(f"{relative_path.as_posix()}:{node.lineno} -> central")
                    if imported_owner == canonical_owner:
                        violations.append(
                            f"{relative_path.as_posix()}:{node.lineno} -> module-style"
                        )
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                imported_parts = tuple(part for part in (node.module or "").split(".") if part)
                resolved = (*package_parts[:keep], *imported_parts)
            imported_watch_symbols = {alias.name for alias in node.names} & watch_symbols
            if imported_watch_symbols and resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )
            if resolved == central_owner:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> central")
            if resolved == ("rail_waitlist",) and any(
                alias.name == "models" for alias in node.names
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> central-module")
            if resolved == ("rail_waitlist", "watch_management") and any(
                alias.name == "models" for alias in node.names
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module-style")

    assert violations == []


def test_confirmation_contract_owner_has_an_exact_import_allowlist() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "reservations" / "provider_confirmation" / "contracts.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert direct_imports == {("re", None)}
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("dataclasses", 0, "dataclass", None),
        ("datetime", 0, "datetime", None),
        ("enum", 0, "StrEnum", None),
        ("typing", 0, "Protocol", None),
        ("domain", 3, "Provider", None),
        ("domain", 3, "SeatClass", None),
        (
            "provider_registry.official_url_policy",
            3,
            "require_official_handoff_url",
            None,
        ),
    }


@pytest.mark.parametrize(
    "relative_path",
    [
        "rail_waitlist/provider_registry/__init__.py",
        "rail_waitlist/reservations/__init__.py",
        "rail_waitlist/reservations/provider_confirmation/__init__.py",
    ],
)
def test_confirmation_outcome_parent_packages_stay_passive(relative_path: str) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.Expr)
    assert isinstance(tree.body[0].value, ast.Constant)
    assert isinstance(tree.body[0].value.value, str)


def test_top_level_confirmation_exactly_aliases_the_canonical_contracts() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservation_confirmation.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    contract_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "reservations.provider_confirmation.contracts"
        and node.level == 1
        for alias in node.names
    }
    policy_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "provider_registry.official_url_policy"
        and node.level == 1
        for alias in node.names
    }
    contract_symbols = {
        "ReservationConfirmationAdapter",
        "ReservationConfirmationOutcome",
        "ReservationConfirmationPurpose",
        "ReservationConfirmationResult",
        "ReservationConfirmationSeat",
        "ReservationConfirmationTarget",
    }
    local_definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {*contract_symbols, "require_official_handoff_url"}
    ]
    local_assignments = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id in {*contract_symbols, "require_official_handoff_url"}
                for target in node.targets
            )
        )
        or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in {*contract_symbols, "require_official_handoff_url"}
        )
    ]

    assert contract_imports == {
        ("reservations.provider_confirmation.contracts", 1, symbol, symbol)
        for symbol in contract_symbols
    }
    assert policy_imports == {
        (
            "provider_registry.official_url_policy",
            1,
            "OFFICIAL_HOST_ROOTS",
            "OFFICIAL_HOST_ROOTS",
        ),
        (
            "provider_registry.official_url_policy",
            1,
            "require_official_handoff_url",
            "require_official_handoff_url",
        ),
    }
    assert local_definitions == []
    assert local_assignments == []


@pytest.mark.parametrize(
    ("relative_path", "expected_module", "expected_level"),
    [
        (
            "rail_waitlist/korail_browser_seat_source.py",
            "reservations.provider_confirmation.contracts",
            1,
        ),
        (
            "rail_waitlist/korail_sidecar/http.py",
            "reservations.provider_confirmation.contracts",
            2,
        ),
        (
            "rail_waitlist/provider_adapters/base.py",
            "reservations.provider_confirmation.contracts",
            2,
        ),
        (
            "rail_waitlist/provider_adapters/srt_execution.py",
            "reservations.provider_confirmation.contracts",
            2,
        ),
        (
            "rail_waitlist/reservations/attempt_policy.py",
            "provider_confirmation.contracts",
            1,
        ),
        (
            "rail_waitlist/reservations/execution_application.py",
            "provider_confirmation.contracts",
            1,
        ),
        (
            "rail_waitlist/reservations/payment_hold_application.py",
            "provider_confirmation.contracts",
            1,
        ),
        (
            "rail_waitlist/reservations/provider_confirmation/korail.py",
            "contracts",
            1,
        ),
        (
            "rail_waitlist/reservations/provider_confirmation/srt.py",
            "contracts",
            1,
        ),
        (
            "rail_waitlist/reservations/reconciliation_application.py",
            "provider_confirmation.contracts",
            1,
        ),
        (
            "rail_waitlist/reservations/reconciliation_state_application.py",
            "provider_confirmation.contracts",
            1,
        ),
        (
            "rail_waitlist/srt_sidecar/contracts.py",
            "reservations.provider_confirmation.contracts",
            2,
        ),
        (
            "rail_waitlist/srt_sidecar/reservation.py",
            "reservations.provider_confirmation.contracts",
            2,
        ),
        (
            "rail_waitlist/watch_management/models.py",
            "reservations.provider_confirmation.contracts",
            2,
        ),
    ],
)
def test_confirmation_outcome_consumers_import_the_direct_canonical_symbol(
    relative_path: str,
    expected_module: str,
    expected_level: int,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (alias.name, None if alias.asname in {None, alias.name} else alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == expected_module
        and node.level == expected_level
        for alias in node.names
        if alias.name == "ReservationConfirmationOutcome"
    }

    assert imports == {("ReservationConfirmationOutcome", None)}


def test_production_confirmation_outcome_references_use_direct_canonical_imports() -> None:
    canonical_owner = (
        "rail_waitlist",
        "reservations",
        "provider_confirmation",
        "contracts",
    )
    canonical_package = canonical_owner[:-1]
    legacy_owner = ("rail_waitlist", "reservation_confirmation")
    excluded_paths = {
        "rail_waitlist/reservation_confirmation.py",
        "rail_waitlist/reservations/provider_confirmation/contracts.py",
    }
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in excluded_paths:
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_owner = tuple(alias.name.split("."))
                    if imported_owner in {canonical_owner, legacy_owner}:
                        violations.append(
                            f"{relative_path.as_posix()}:{node.lineno} -> module-style"
                        )
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                imported_parts = tuple(part for part in (node.module or "").split(".") if part)
                resolved = (*package_parts[:keep], *imported_parts)
            imported_names = {alias.name for alias in node.names}
            if "ReservationConfirmationOutcome" in imported_names and resolved != canonical_owner:
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )
            if resolved == canonical_owner and "*" in imported_names:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> wildcard")
            if resolved == canonical_package and "contracts" in imported_names:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module-style")
            if resolved == ("rail_waitlist",) and "reservation_confirmation" in imported_names:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> legacy-module")
            if resolved == legacy_owner and "*" in imported_names:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> legacy-wildcard")

    assert violations == []


def test_official_url_policy_owner_has_an_exact_import_allowlist() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_registry" / "official_url_policy.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import)]
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert direct_imports == []
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("typing", 0, "Protocol", None),
        ("urllib.parse", 0, "urlsplit", None),
        ("domain", 2, "Provider", None),
    }


def test_central_schemas_exactly_aliases_the_official_url_policy() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols = {"OFFICIAL_HOST_ROOTS", "is_official_provider_host"}
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "provider_registry.official_url_policy"
        and node.level == 1
        for alias in node.names
        if alias.name in symbols
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in symbols
    ]
    assignments = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id in symbols for target in node.targets
            )
        )
        or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in symbols
        )
    ]

    assert imports == {
        ("provider_registry.official_url_policy", 1, symbol, symbol) for symbol in symbols
    }
    assert definitions == []
    assert assignments == []


@pytest.mark.parametrize(
    ("relative_path", "expected_module", "expected_level", "expected_symbols"),
    [
        (
            "rail_waitlist/korail_browser_seat_source.py",
            "reservations.provider_confirmation.contracts",
            1,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/korail_pydoll_browser.py",
            "reservations.provider_confirmation.contracts",
            1,
            {"ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/korail_sidecar/pydoll/confirmation_reader.py",
            "reservations.provider_confirmation.contracts",
            3,
            {"ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/korail_sidecar/http.py",
            "reservations.provider_confirmation.contracts",
            2,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/provider_adapters/base.py",
            "reservations.provider_confirmation.contracts",
            2,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/provider_adapters/korail_execution.py",
            "reservations.provider_confirmation.contracts",
            2,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/provider_adapters/srt_execution.py",
            "reservations.provider_confirmation.contracts",
            2,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/provider_contracts.py",
            "reservations.provider_confirmation.contracts",
            1,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/reservations/attempt_result_application.py",
            "provider_confirmation.contracts",
            1,
            {"ReservationConfirmationResult"},
        ),
        (
            "rail_waitlist/reservations/execution_application.py",
            "provider_confirmation.contracts",
            1,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/reservations/provider_confirmation/korail.py",
            "contracts",
            1,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/reservations/provider_confirmation/srt.py",
            "contracts",
            1,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/reservations/reconciliation_application.py",
            "provider_confirmation.contracts",
            1,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/reservations/reconciliation_state_application.py",
            "provider_confirmation.contracts",
            1,
            {"ReservationConfirmationResult"},
        ),
        (
            "rail_waitlist/services.py",
            "reservations.provider_confirmation.contracts",
            1,
            {"ReservationConfirmationResult"},
        ),
        (
            "rail_waitlist/srt_sidecar/client.py",
            "reservations.provider_confirmation.contracts",
            2,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/srt_sidecar/contracts.py",
            "reservations.provider_confirmation.contracts",
            2,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/srt_sidecar/application.py",
            "reservations.provider_confirmation.contracts",
            2,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
        (
            "rail_waitlist/srt_sidecar/reservation.py",
            "reservations.provider_confirmation.contracts",
            2,
            {"ReservationConfirmationResult", "ReservationConfirmationTarget"},
        ),
    ],
)
def test_confirmation_contract_consumers_use_direct_canonical_symbols(
    relative_path: str,
    expected_module: str,
    expected_level: int,
    expected_symbols: set[str],
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    contract_symbols = {
        "ReservationConfirmationAdapter",
        "ReservationConfirmationResult",
        "ReservationConfirmationTarget",
    }
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == expected_module
        and node.level == expected_level
        for alias in node.names
        if alias.name in contract_symbols
    }

    assert imports == {(symbol, None) for symbol in expected_symbols}


@pytest.mark.parametrize(
    (
        "relative_path",
        "expected_module",
        "expected_level",
        "expected_symbols",
        "same_name_alias",
    ),
    [
        (
            "rail_waitlist/schemas.py",
            "provider_registry.official_url_policy",
            1,
            {"OFFICIAL_HOST_ROOTS", "is_official_provider_host"},
            True,
        ),
        (
            "rail_waitlist/reservation_confirmation.py",
            "provider_registry.official_url_policy",
            1,
            {"OFFICIAL_HOST_ROOTS", "require_official_handoff_url"},
            True,
        ),
        (
            "rail_waitlist/reservations/provider_confirmation/korail.py",
            "provider_registry.official_url_policy",
            3,
            {"require_official_handoff_url"},
            False,
        ),
        (
            "rail_waitlist/reservations/provider_confirmation/srt.py",
            "provider_registry.official_url_policy",
            3,
            {"require_official_handoff_url"},
            False,
        ),
    ],
)
def test_official_url_policy_consumers_use_direct_canonical_symbols(
    relative_path: str,
    expected_module: str,
    expected_level: int,
    expected_symbols: set[str],
    same_name_alias: bool,
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == expected_module
        and node.level == expected_level
        for alias in node.names
        if alias.name in expected_symbols
    }

    expected_alias = (lambda symbol: symbol) if same_name_alias else (lambda _symbol: None)
    assert imports == {(symbol, expected_alias(symbol)) for symbol in expected_symbols}


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _legacy_reservation_confirmation_imports(source: str, relative_path: Path) -> list[str]:
    legacy_owner = ("rail_waitlist", "reservation_confirmation")
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []
    package_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "rail_waitlist":
                    package_aliases.add(alias.asname or "rail_waitlist")
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or "importlib")
                if tuple(alias.name.split(".")) == legacy_owner:
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        if resolved == legacy_owner:
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == ("rail_waitlist",) and any(
            alias.name == "reservation_confirmation" for alias in node.names
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> module-style")
        if resolved == ("rail_waitlist",):
            package_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "rail_waitlist"
            )
        if resolved == ("importlib",):
            import_module_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = _attribute_chain(node)
            if (
                len(parts) >= 2
                and parts[0] in package_aliases
                and parts[1] == ("reservation_confirmation")
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-attribute")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        dynamic_import = (
            isinstance(node.func, ast.Name) and node.func.id in import_module_aliases
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in importlib_aliases
            and node.func.attr == "import_module"
        )
        if (
            dynamic_import
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.reservation_confirmation"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> importlib")

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from .reservation_confirmation import ReservationConfirmationTarget",
        "from rail_waitlist.reservation_confirmation import ReservationConfirmationTarget",
        "import rail_waitlist.reservation_confirmation",
        "import rail_waitlist.reservation_confirmation as confirmation",
        "from rail_waitlist import reservation_confirmation",
        "from . import reservation_confirmation",
        "from .reservation_confirmation import *",
        "import importlib; importlib.import_module('rail_waitlist.reservation_confirmation')",
        "from importlib import import_module; "
        "import_module('rail_waitlist.reservation_confirmation')",
        "import rail_waitlist as rw; rw.reservation_confirmation.ReservationConfirmationTarget",
    ],
)
def test_legacy_reservation_confirmation_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _legacy_reservation_confirmation_imports(source, Path("rail_waitlist/probe.py"))


def test_production_does_not_reenter_the_legacy_reservation_confirmation_facade() -> None:
    violations: list[str] = []
    facade_path = "rail_waitlist/reservation_confirmation.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == facade_path:
            continue
        violations.extend(
            _legacy_reservation_confirmation_imports(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def _legacy_schema_url_policy_references(source: str, relative_path: Path) -> list[str]:
    central_owner = ("rail_waitlist", "schemas")
    policy_symbols = {"OFFICIAL_HOST_ROOTS", "is_official_provider_host"}
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []
    package_aliases: set[str] = set()
    schema_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = tuple(alias.name.split("."))
                if imported == ("rail_waitlist",):
                    package_aliases.add(alias.asname or "rail_waitlist")
                if imported == central_owner:
                    if alias.asname is None:
                        package_aliases.add("rail_waitlist")
                    else:
                        schema_aliases.add(alias.asname)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        imported_names = {alias.name for alias in node.names}
        if resolved == central_owner and (imported_names & policy_symbols or "*" in imported_names):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == ("rail_waitlist",):
            schema_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "schemas"
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parts = _attribute_chain(node)
        if len(parts) >= 2 and parts[0] in schema_aliases and parts[-1] in policy_symbols:
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> schema-attribute")
        if (
            len(parts) >= 3
            and parts[0] in package_aliases
            and parts[1] == "schemas"
            and parts[-1] in policy_symbols
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-attribute")

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import OFFICIAL_HOST_ROOTS",
        "from rail_waitlist.schemas import is_official_provider_host",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.OFFICIAL_HOST_ROOTS",
        "from rail_waitlist import schemas as s; s.is_official_provider_host",
        "import rail_waitlist as rw; rw.schemas.OFFICIAL_HOST_ROOTS",
    ],
)
def test_legacy_schema_url_policy_detector_rejects_all_access_forms(source: str) -> None:
    assert _legacy_schema_url_policy_references(source, Path("rail_waitlist/probe.py"))


def test_legacy_schema_url_policy_detector_allows_other_package_attributes() -> None:
    source = (
        "import rail_waitlist.schemas; "
        "rail_waitlist.provider_registry.official_url_policy.OFFICIAL_HOST_ROOTS"
    )
    assert _legacy_schema_url_policy_references(source, Path("rail_waitlist/probe.py")) == []


def test_production_does_not_reenter_central_schemas_for_official_url_policy() -> None:
    violations: list[str] = []
    central_path = "rail_waitlist/schemas.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _legacy_schema_url_policy_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_observation_contract_owner_has_a_lightweight_exact_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "observations" / "contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "datetime", None),
        ("typing", 0, "Literal", None),
        ("pydantic", 0, "Field", None),
        ("pydantic", 0, "field_validator", None),
        ("pydantic", 0, "model_validator", None),
        ("domain", 2, "Provider", None),
        ("domain", 2, "SeatClass", None),
        ("domain", 2, "SeatObservationStatus", None),
        ("provider_schema_base", 2, "ProviderContractModel", None),
    }


def test_observations_package_remains_a_passive_namespace() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "observations" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


def test_central_schema_hub_only_aliases_observation_contracts() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols = {
        "ObservationErrorCategory",
        "SeatObservationRequest",
        "SeatObservationResult",
    }
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "observations.contracts"
        and node.level == 1
        for alias in node.names
    }
    local_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef) and node.name in symbols
    }
    local_assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name) and target.id in symbols
    }

    assert imports == {(symbol, symbol) for symbol in symbols}
    assert local_classes == set()
    assert local_assignments == set()


@pytest.mark.parametrize(
    ("relative_path", "canonical_module", "canonical_level", "expected_symbols"),
    [
        (
            "rail_waitlist/approved_provider.py",
            "observations.contracts",
            1,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/korail_browser_seat_source.py",
            "observations.contracts",
            1,
            {
                "ObservationErrorCategory",
                "SeatObservationRequest",
                "SeatObservationResult",
            },
        ),
        (
            "rail_waitlist/observations/group_application.py",
            "contracts",
            1,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/observations/operational_projection_application.py",
            "contracts",
            1,
            {"SeatObservationResult"},
        ),
        (
            "rail_waitlist/observations/recording_application.py",
            "contracts",
            1,
            {"SeatObservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/base.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/korail_execution.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/mock.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/korail_browser_observation_policy.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/srt_execution.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/srt_source_runtime.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/provider_contracts.py",
            "observations.contracts",
            1,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/services.py",
            "observations.contracts",
            1,
            {"SeatObservationResult"},
        ),
        (
            "rail_waitlist/srt_sidecar/client.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/srt_sidecar/contracts.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/srt_sidecar/application.py",
            "observations.contracts",
            2,
            {"SeatObservationRequest", "SeatObservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/srt_seat_source.py",
            "observations.contracts",
            2,
            {
                "ObservationErrorCategory",
                "SeatObservationRequest",
                "SeatObservationResult",
            },
        ),
        (
            "rail_waitlist/worker.py",
            "observations.contracts",
            1,
            {"SeatObservationResult"},
        ),
    ],
)
def test_observation_contract_consumers_use_direct_canonical_symbols(
    relative_path: str,
    canonical_module: str,
    canonical_level: int,
    expected_symbols: set[str],
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    contract_symbols = {
        "ObservationErrorCategory",
        "SeatObservationRequest",
        "SeatObservationResult",
    }
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == canonical_module
        and node.level == canonical_level
        for alias in node.names
        if alias.name in contract_symbols
    }

    assert imports == {(symbol, None) for symbol in expected_symbols}


def _central_schema_contract_references(
    source: str,
    relative_path: Path,
    contract_symbols: set[str],
) -> list[str]:
    central_owner = ("rail_waitlist", "schemas")
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []
    package_aliases: set[str] = set()
    schema_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = tuple(alias.name.split("."))
                if imported == ("rail_waitlist",):
                    package_aliases.add(alias.asname or "rail_waitlist")
                if imported == central_owner:
                    if alias.asname is None:
                        package_aliases.add("rail_waitlist")
                    else:
                        schema_aliases.add(alias.asname)
                if imported == ("importlib",):
                    importlib_aliases.add(alias.asname or "importlib")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        imported_names = {alias.name for alias in node.names}
        if resolved == central_owner and (
            imported_names & contract_symbols or "*" in imported_names
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == ("rail_waitlist",):
            schema_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "schemas"
            )
        if resolved == ("importlib",):
            import_module_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if value is None:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(value, ast.Name) and value.id in schema_aliases:
                    before = len(schema_aliases)
                    schema_aliases.add(target.id)
                    changed = changed or len(schema_aliases) != before
                if isinstance(value, ast.Name) and value.id in package_aliases:
                    before = len(package_aliases)
                    package_aliases.add(target.id)
                    changed = changed or len(package_aliases) != before
                parts = _attribute_chain(value)
                if len(parts) == 2 and parts[0] in package_aliases and parts[1] == "schemas":
                    before = len(schema_aliases)
                    schema_aliases.add(target.id)
                    changed = changed or len(schema_aliases) != before

    def is_schema_reference(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in schema_aliases
        parts = _attribute_chain(node)
        return len(parts) == 2 and parts[0] in package_aliases and parts[1] == "schemas"

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = _attribute_chain(node)
            if len(parts) >= 2 and parts[0] in schema_aliases and parts[-1] in contract_symbols:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> schema-attribute")
            if (
                len(parts) >= 3
                and parts[0] in package_aliases
                and parts[1] == "schemas"
                and parts[-1] in contract_symbols
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-attribute")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and is_schema_reference(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in contract_symbols
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> getattr")
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.schemas"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> __import__")
        dynamic_import = (
            isinstance(node.func, ast.Name) and node.func.id in import_module_aliases
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in importlib_aliases
            and node.func.attr == "import_module"
        )
        if (
            dynamic_import
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.schemas"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> importlib")

    return violations


def _central_schema_observation_contract_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    return _central_schema_contract_references(
        source,
        relative_path,
        {
            "ObservationErrorCategory",
            "SeatObservationRequest",
            "SeatObservationResult",
        },
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import SeatObservationRequest",
        "from rail_waitlist.schemas import SeatObservationResult",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.ObservationErrorCategory",
        "from rail_waitlist import schemas as s; s.SeatObservationRequest",
        "import rail_waitlist as rw; rw.schemas.SeatObservationResult",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module; import_module('rail_waitlist.schemas')",
        "import rail_waitlist.schemas as s; alias = s; alias.SeatObservationRequest",
        "import rail_waitlist.schemas as s; getattr(s, 'SeatObservationResult')",
        "__import__('rail_waitlist.schemas').schemas.SeatObservationRequest",
        "import rail_waitlist as rw; s = rw.schemas; s.ObservationErrorCategory",
    ],
)
def test_central_schema_observation_contract_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _central_schema_observation_contract_references(
        source,
        Path("rail_waitlist/probe.py"),
    )


def test_central_schema_observation_contract_detector_allows_other_symbols() -> None:
    source = (
        "from .schemas import ReservationRequest; "
        "import rail_waitlist.schemas; "
        "rail_waitlist.observations.contracts.SeatObservationResult"
    )
    assert (
        _central_schema_observation_contract_references(
            source,
            Path("rail_waitlist/probe.py"),
        )
        == []
    )


def test_production_does_not_reenter_central_schemas_for_observation_contracts() -> None:
    violations: list[str] = []
    central_path = "rail_waitlist/schemas.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _central_schema_observation_contract_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_reservation_contract_owner_has_a_lightweight_exact_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservations" / "contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "datetime", None),
        ("typing", 0, "Literal", None),
        ("pydantic", 0, "AnyHttpUrl", None),
        ("pydantic", 0, "Field", None),
        ("pydantic", 0, "field_validator", None),
        ("pydantic", 0, "model_validator", None),
        ("domain", 2, "Provider", None),
        ("domain", 2, "ReservationOutcome", None),
        ("observations.contracts", 2, "SeatObservationRequest", None),
        ("provider_registry.official_url_policy", 2, "OFFICIAL_HOST_ROOTS", None),
        ("provider_registry.official_url_policy", 2, "is_official_provider_host", None),
        ("provider_schema_base", 2, "ProviderContractModel", None),
    }


def test_reservations_package_remains_a_passive_namespace() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservations" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


def test_central_schema_hub_only_aliases_reservation_contracts() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols = {
        "ReservationProgressStage",
        "ReservationProgressStageName",
        "ReservationRequest",
        "ReservationResult",
    }
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "reservations.contracts"
        and node.level == 1
        for alias in node.names
    }
    local_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef) and node.name in symbols
    }
    local_assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name) and target.id in symbols
    }

    assert imports == {(symbol, symbol) for symbol in symbols}
    assert local_classes == set()
    assert local_assignments == set()


@pytest.mark.parametrize(
    ("relative_path", "canonical_module", "canonical_level", "expected_symbols"),
    [
        (
            "rail_waitlist/approved_provider.py",
            "reservations.contracts",
            1,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/korail_browser_seat_source.py",
            "reservations.contracts",
            1,
            {"ReservationProgressStage", "ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/base.py",
            "reservations.contracts",
            2,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/korail_execution.py",
            "reservations.contracts",
            2,
            {"ReservationProgressStage", "ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/mock.py",
            "reservations.contracts",
            2,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/provider_adapters/srt_execution.py",
            "reservations.contracts",
            2,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/provider_contracts.py",
            "reservations.contracts",
            1,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/reservations/attempt_result_application.py",
            "contracts",
            1,
            {"ReservationResult"},
        ),
        (
            "rail_waitlist/reservations/execution_application.py",
            "contracts",
            1,
            {"ReservationProgressStage", "ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/services.py",
            "reservations.contracts",
            1,
            {"ReservationResult"},
        ),
        (
            "rail_waitlist/srt_sidecar/client.py",
            "reservations.contracts",
            2,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/srt_sidecar/contracts.py",
            "reservations.contracts",
            2,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/srt_sidecar/application.py",
            "reservations.contracts",
            2,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/srt_sidecar/reservation.py",
            "reservations.contracts",
            2,
            {"ReservationRequest", "ReservationResult"},
        ),
        (
            "rail_waitlist/watch_management/http.py",
            "reservations.contracts",
            2,
            {"ReservationResult"},
        ),
    ],
)
def test_reservation_contract_consumers_use_direct_canonical_symbols(
    relative_path: str,
    canonical_module: str,
    canonical_level: int,
    expected_symbols: set[str],
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    contract_symbols = {
        "ReservationProgressStage",
        "ReservationProgressStageName",
        "ReservationRequest",
        "ReservationResult",
    }
    imports = {
        (alias.name, None if alias.asname in {None, alias.name} else alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == canonical_module
        and node.level == canonical_level
        for alias in node.names
        if alias.name in contract_symbols
    }

    assert imports == {(symbol, None) for symbol in expected_symbols}


def _central_schema_reservation_contract_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    return _central_schema_contract_references(
        source,
        relative_path,
        {
            "ReservationProgressStage",
            "ReservationProgressStageName",
            "ReservationRequest",
            "ReservationResult",
        },
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import ReservationRequest",
        "from rail_waitlist.schemas import ReservationResult",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.ReservationProgressStage",
        "from rail_waitlist import schemas as s; s.ReservationProgressStageName",
        "import rail_waitlist as rw; rw.schemas.ReservationRequest",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module; import_module('rail_waitlist.schemas')",
        "import rail_waitlist.schemas as s; alias = s; alias.ReservationResult",
        "import rail_waitlist.schemas as s; getattr(s, 'ReservationRequest')",
        "__import__('rail_waitlist.schemas').schemas.ReservationResult",
        "import rail_waitlist as rw; s = rw.schemas; s.ReservationProgressStage",
    ],
)
def test_central_schema_reservation_contract_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _central_schema_reservation_contract_references(
        source,
        Path("rail_waitlist/probe.py"),
    )


def test_central_schema_reservation_contract_detector_allows_other_symbols() -> None:
    source = (
        "from .schemas import ProviderCapabilities; "
        "import rail_waitlist.schemas; "
        "rail_waitlist.reservations.contracts.ReservationResult"
    )
    assert (
        _central_schema_reservation_contract_references(
            source,
            Path("rail_waitlist/probe.py"),
        )
        == []
    )


def test_production_does_not_reenter_central_schemas_for_reservation_contracts() -> None:
    violations: list[str] = []
    central_path = "rail_waitlist/schemas.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _central_schema_reservation_contract_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


TIMETABLE_TRANSPORT_SYMBOLS = {
    "SeatAvailabilityStatus",
    "SeatAvailability",
    "SeatAvailabilityNotObservedReason",
    "SeatAvailabilityProvenance",
    "SeatAvailabilityAction",
    "SeatClassAvailability",
    "TimetableSeatEvidenceRead",
    "TimetableItem",
}


def test_timetable_transport_owner_has_an_exact_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "timetable_management" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "datetime", None),
        ("datetime", 0, "timezone", None),
        ("typing", 0, "Any", None),
        ("typing", 0, "Literal", None),
        ("pydantic", 0, "AnyHttpUrl", None),
        ("pydantic", 0, "Field", None),
        ("pydantic", 0, "field_validator", None),
        ("pydantic", 0, "model_validator", None),
        ("browser_companion.schemas", 2, "KORAIL_BROWSER_COMPANION_SOURCE", None),
        ("domain", 2, "Provider", None),
        ("domain", 2, "SeatClass", None),
        (
            "provider_registry.korail_search_url_policy",
            2,
            "validate_korail_general_search_url",
            None,
        ),
        ("official_page_confirmation.schemas", 2, "OFFICIAL_PAGE_CONFIRMATION_SOURCE", None),
        ("provider_registry.official_url_policy", 2, "is_official_provider_host", None),
        ("schema_base", 2, "ApiModel", None),
    }


def test_timetable_transport_owner_defines_the_exact_aggregate_bodies() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "timetable_management" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    owned_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in TIMETABLE_TRANSPORT_SYMBOLS
    }
    owned_assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name) and target.id in TIMETABLE_TRANSPORT_SYMBOLS
    }

    assert owned_classes == {
        "SeatAvailability",
        "SeatAvailabilityProvenance",
        "SeatAvailabilityAction",
        "SeatClassAvailability",
        "TimetableSeatEvidenceRead",
        "TimetableItem",
    }
    assert owned_assignments == {
        "SeatAvailabilityStatus",
        "SeatAvailabilityNotObservedReason",
    }


def test_timetable_management_package_remains_a_passive_namespace() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "timetable_management" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


def test_central_schema_hub_only_aliases_timetable_transport_contracts() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    local_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in TIMETABLE_TRANSPORT_SYMBOLS
    }
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in TIMETABLE_TRANSPORT_SYMBOLS
    }

    assert local_classes == set()
    assert set(aliases) == TIMETABLE_TRANSPORT_SYMBOLS
    for symbol, value in aliases.items():
        assert isinstance(value, ast.Attribute)
        assert value.attr == symbol
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "timetable_management_schemas"


@pytest.mark.parametrize(
    ("relative_path", "canonical_module", "canonical_level", "expected_symbols"),
    [
        (
            "rail_waitlist/approved_provider.py",
            "timetable_management.schemas",
            1,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/browser_companion/snapshot_overlay.py",
            "timetable_management.schemas",
            2,
            {
                "SeatAvailabilityAction",
                "SeatAvailabilityProvenance",
                "SeatClassAvailability",
                "TimetableItem",
            },
        ),
        (
            "rail_waitlist/korail_browser_seat_source.py",
            "timetable_management.schemas",
            1,
            {
                "SeatAvailability",
                "SeatAvailabilityAction",
                "SeatAvailabilityNotObservedReason",
                "SeatAvailabilityProvenance",
                "SeatClassAvailability",
                "TimetableItem",
            },
        ),
        (
            "rail_waitlist/provider_adapters/korail_seat_source.py",
            "timetable_management.schemas",
            2,
            {
                "SeatAvailabilityAction",
                "SeatAvailabilityNotObservedReason",
                "SeatAvailabilityProvenance",
                "SeatAvailabilityStatus",
                "SeatClassAvailability",
                "TimetableItem",
            },
        ),
        (
            "rail_waitlist/official_page_confirmation/application.py",
            "timetable_management.schemas",
            2,
            {
                "SeatAvailabilityAction",
                "SeatAvailabilityProvenance",
                "SeatClassAvailability",
                "TimetableItem",
            },
        ),
        (
            "rail_waitlist/provider_adapters/base.py",
            "timetable_management.schemas",
            2,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/provider_adapters/execution.py",
            "timetable_management.schemas",
            2,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/provider_adapters/experimental.py",
            "timetable_management.schemas",
            2,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/provider_adapters/korail_execution.py",
            "timetable_management.schemas",
            2,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/provider_adapters/mock.py",
            "timetable_management.schemas",
            2,
            {
                "SeatAvailability",
                "SeatAvailabilityAction",
                "SeatAvailabilityProvenance",
                "SeatClassAvailability",
                "TimetableItem",
            },
        ),
        (
            "rail_waitlist/provider_adapters/srt_execution.py",
            "timetable_management.schemas",
            2,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/provider_adapters/tago.py",
            "timetable_management.schemas",
            2,
            {"SeatAvailability", "TimetableItem"},
        ),
        (
            "rail_waitlist/provider_adapters/timetable.py",
            "timetable_management.schemas",
            2,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/provider_adapters/timetable_support.py",
            "timetable_management.schemas",
            2,
            {
                "SeatAvailabilityAction",
                "SeatAvailabilityNotObservedReason",
                "SeatAvailabilityProvenance",
                "SeatClassAvailability",
            },
        ),
        (
            "rail_waitlist/provider_contracts.py",
            "timetable_management.schemas",
            1,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/providers.py",
            "timetable_management.schemas",
            1,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/seat_status_cooldown.py",
            "timetable_management.schemas",
            1,
            {"SeatAvailabilityNotObservedReason"},
        ),
        (
            "rail_waitlist/srt_sidecar/client.py",
            "timetable_management.schemas",
            2,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/srt_sidecar/contracts.py",
            "timetable_management.schemas",
            2,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/srt_sidecar/application.py",
            "timetable_management.schemas",
            2,
            {"SeatAvailabilityStatus", "TimetableItem"},
        ),
        (
            "rail_waitlist/provider_adapters/srt_seat_source.py",
            "timetable_management.schemas",
            2,
            {
                "SeatAvailabilityAction",
                "SeatAvailabilityNotObservedReason",
                "SeatAvailabilityProvenance",
                "SeatAvailabilityStatus",
                "SeatClassAvailability",
                "TimetableItem",
            },
        ),
        (
            "rail_waitlist/timetable_evidence.py",
            "timetable_management.schemas",
            1,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/timetable_management/application.py",
            "schemas",
            1,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/timetable_management/contracts.py",
            "schemas",
            1,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/timetable_management/http.py",
            "schemas",
            1,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/timetable_management/tago_timetable_projection.py",
            "schemas",
            1,
            {
                "SeatAvailability",
                "SeatAvailabilityNotObservedReason",
                "SeatClassAvailability",
                "TimetableItem",
            },
        ),
        (
            "rail_waitlist/timetable_management/srt_live_timetable.py",
            "schemas",
            1,
            {
                "SeatAvailability",
                "SeatAvailabilityAction",
                "SeatAvailabilityProvenance",
                "SeatClassAvailability",
                "TimetableItem",
            },
        ),
        (
            "rail_waitlist/timetable_snapshot_cache.py",
            "timetable_management.schemas",
            1,
            {"TimetableItem"},
        ),
        (
            "rail_waitlist/watch_registration_policy.py",
            "timetable_management.schemas",
            1,
            {"TimetableItem"},
        ),
    ],
)
def test_timetable_transport_consumers_use_direct_canonical_symbols(
    relative_path: str,
    canonical_module: str,
    canonical_level: int,
    expected_symbols: set[str],
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == canonical_module
        and node.level == canonical_level
        for alias in node.names
        if alias.name in TIMETABLE_TRANSPORT_SYMBOLS
    }

    assert imports == expected_symbols


def _central_schema_timetable_transport_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    return _central_schema_contract_references(
        source,
        relative_path,
        TIMETABLE_TRANSPORT_SYMBOLS,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import TimetableItem",
        "from rail_waitlist.schemas import SeatClassAvailability",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.SeatAvailability",
        "from rail_waitlist import schemas as s; s.SeatAvailabilityProvenance",
        "import rail_waitlist as rw; rw.schemas.SeatAvailabilityAction",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module; import_module('rail_waitlist.schemas')",
        "import rail_waitlist.schemas as s; alias = s; alias.TimetableSeatEvidenceRead",
        "import rail_waitlist.schemas as s; getattr(s, 'SeatAvailabilityStatus')",
        "__import__('rail_waitlist.schemas').schemas.TimetableItem",
        "import rail_waitlist as rw; s = rw.schemas; s.SeatAvailabilityNotObservedReason",
    ],
)
def test_central_schema_timetable_transport_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _central_schema_timetable_transport_references(
        source,
        Path("rail_waitlist/probe.py"),
    )


def test_central_schema_timetable_transport_detector_allows_other_symbols() -> None:
    source = (
        "from .schemas import ProviderCapabilities; "
        "import rail_waitlist.schemas; "
        "rail_waitlist.timetable_management.schemas.TimetableItem"
    )
    assert (
        _central_schema_timetable_transport_references(
            source,
            Path("rail_waitlist/probe.py"),
        )
        == []
    )


def test_production_does_not_reenter_central_schemas_for_timetable_transport() -> None:
    violations: list[str] = []
    central_path = "rail_waitlist/schemas.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _central_schema_timetable_transport_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


WATCH_READ_SYMBOLS = {
    "WatchCandidateLatestReservationAttemptRead",
    "WatchCandidateRead",
    "WatchCandidateLatestObservationRead",
    "WatchRead",
}


def _central_schema_watch_read_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    return _central_schema_contract_references(
        source,
        relative_path,
        WATCH_READ_SYMBOLS,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import WatchRead",
        "from rail_waitlist.schemas import WatchCandidateRead",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.WatchRead",
        "from rail_waitlist import schemas as s; s.WatchCandidateLatestObservationRead",
        "import rail_waitlist as rw; rw.schemas.WatchCandidateRead",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module; import_module('rail_waitlist.schemas')",
        "import rail_waitlist.schemas as s; alias = s; alias.WatchRead",
        "import rail_waitlist.schemas as s; getattr(s, 'WatchCandidateRead')",
        "__import__('rail_waitlist.schemas').schemas.WatchRead",
        "import rail_waitlist as rw; s = rw.schemas; s.WatchRead",
    ],
)
def test_central_schema_watch_read_detector_rejects_all_access_forms(source: str) -> None:
    assert _central_schema_watch_read_references(
        source,
        Path("rail_waitlist/probe.py"),
    )


def test_production_does_not_reenter_central_schemas_for_watch_reads() -> None:
    violations: list[str] = []
    central_path = "rail_waitlist/schemas.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _central_schema_watch_read_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_watch_read_consumers_are_the_exact_canonical_set() -> None:
    direct_consumers: dict[str, set[str]] = {}

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in {
            "rail_waitlist/schemas.py",
            "rail_waitlist/watch_management/schemas.py",
        }:
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "schemas"
            and node.level == 1
            and relative_path.parent.as_posix() == "rail_waitlist/watch_management"
            for alias in node.names
            if alias.name in WATCH_READ_SYMBOLS
        }
        if imports:
            direct_consumers[relative_path.as_posix()] = imports

    assert direct_consumers == {
        "rail_waitlist/watch_management/http.py": {"WatchRead"},
        "rail_waitlist/watch_management/read_model.py": {"WatchRead"},
    }


PROVIDER_CAPABILITY_SYMBOLS = {"ProviderCapabilities"}


def _central_schema_provider_capability_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    return _central_schema_contract_references(
        source,
        relative_path,
        PROVIDER_CAPABILITY_SYMBOLS,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import ProviderCapabilities",
        "from rail_waitlist.schemas import ProviderCapabilities",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.ProviderCapabilities",
        "from rail_waitlist import schemas as s; s.ProviderCapabilities",
        "import rail_waitlist as rw; rw.schemas.ProviderCapabilities",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module; import_module('rail_waitlist.schemas')",
        "import rail_waitlist.schemas as s; alias = s; alias.ProviderCapabilities",
        "import rail_waitlist.schemas as s; getattr(s, 'ProviderCapabilities')",
        "__import__('rail_waitlist.schemas').schemas.ProviderCapabilities",
        "import rail_waitlist as rw; s = rw.schemas; s.ProviderCapabilities",
    ],
)
def test_central_schema_provider_capability_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _central_schema_provider_capability_references(
        source,
        Path("rail_waitlist/probe.py"),
    )


def test_central_schema_provider_capability_detector_allows_canonical_owner() -> None:
    source = (
        "from .schemas import HealthResponse; "
        "import rail_waitlist.schemas; "
        "from rail_waitlist.provider_registry.contracts import ProviderCapabilities"
    )
    assert (
        _central_schema_provider_capability_references(
            source,
            Path("rail_waitlist/probe.py"),
        )
        == []
    )


def test_production_does_not_reenter_central_schemas_for_provider_capabilities() -> None:
    violations: list[str] = []
    central_path = "rail_waitlist/schemas.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _central_schema_provider_capability_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_provider_capability_operational_consumers_are_the_exact_canonical_set() -> None:
    owner = ("rail_waitlist", "provider_registry", "contracts")
    direct_consumers: dict[str, set[str]] = {}

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in {
            "rail_waitlist/provider_registry/contracts.py",
            "rail_waitlist/providers.py",
            "rail_waitlist/schemas.py",
        }:
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                imported_parts = tuple(part for part in (node.module or "").split(".") if part)
                resolved = (*package_parts[:keep], *imported_parts)
            if resolved == owner:
                imports.update(
                    alias.name for alias in node.names if alias.name in PROVIDER_CAPABILITY_SYMBOLS
                )
        if imports:
            direct_consumers[relative_path.as_posix()] = imports

    assert direct_consumers == {
        "rail_waitlist/approved_provider.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_adapters/base.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_adapters/execution.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_adapters/experimental.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_adapters/korail_execution.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_adapters/mock.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_adapters/srt_execution.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_adapters/timetable.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_contracts.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_registry/application.py": {"ProviderCapabilities"},
        "rail_waitlist/provider_registry/http.py": {"ProviderCapabilities"},
    }


RESERVATION_ATTEMPT_RUNTIME_SYMBOLS = {
    "begin_reservation_attempt",
    "complete_reservation_attempt",
}
LEGACY_SERVICE_RESERVATION_ATTEMPT_SYMBOLS = {
    *RESERVATION_ATTEMPT_RUNTIME_SYMBOLS,
    "reservation_attempt_result_policy",
}


def test_reservation_attempt_runtime_has_an_exact_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "reservations" / "attempt_runtime.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "UTC", None),
        ("datetime", 0, "datetime", None),
        ("sqlalchemy.ext.asyncio", 0, "AsyncSession", None),
        ("observations.status_policy", 2, "ACTIONABLE_SEAT_STATUSES", None),
        ("outbox", 2, "add_outbox_event", None),
        ("watch_management.models", 2, "ReservationAttempt", None),
        ("watch_management.models", 2, "Watch", None),
        ("watch_management.models", 2, "WatchCandidate", None),
        ("watch_management.transition_runtime", 2, "apply_watch_transition", None),
        ("attempt_claim_application", 1, "ReservationAttemptClaimDependencies", None),
        (
            "attempt_claim_application",
            1,
            "begin_reservation_attempt",
            "begin_reservation_attempt_application",
        ),
        ("attempt_policy", 1, "is_confirmed_absent_retry_source", None),
        ("attempt_result_application", 1, "ReservationAttemptResultDependencies", None),
        (
            "attempt_result_application",
            1,
            "complete_reservation_attempt",
            "complete_reservation_attempt_application",
        ),
        ("attempt_result_application", 1, "record_reservation_confirmation", None),
        ("contracts", 1, "ReservationResult", None),
        ("domain", 1, "reservation_attempt_result_policy", None),
        ("payment_hold_application", 1, "is_payment_hold_ended", None),
        (
            "provider_confirmation.contracts",
            1,
            "ReservationConfirmationResult",
            None,
        ),
    }
    assert called_attributes.isdisjoint({"commit", "rollback", "refresh"})
    assert all(
        node.module != "fastapi" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )


def test_reservation_attempt_runtime_has_the_exact_production_consumers() -> None:
    owner = ("rail_waitlist", "reservations", "attempt_runtime")
    direct_consumers: dict[str, set[str]] = {}

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == "rail_waitlist/reservations/attempt_runtime.py":
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                imported_parts = tuple(part for part in (node.module or "").split(".") if part)
                resolved = (*package_parts[:keep], *imported_parts)
            if resolved == owner:
                imports.update(
                    alias.name
                    for alias in node.names
                    if alias.name in RESERVATION_ATTEMPT_RUNTIME_SYMBOLS
                )
        if imports:
            direct_consumers[relative_path.as_posix()] = imports

    assert direct_consumers == {
        "rail_waitlist/watch_management/http.py": RESERVATION_ATTEMPT_RUNTIME_SYMBOLS,
        "rail_waitlist/worker.py": RESERVATION_ATTEMPT_RUNTIME_SYMBOLS,
    }


def test_watch_read_model_uses_the_reservation_domain_policy_directly() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "read_model.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    policy_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "reservation_attempt_result_policy"
    }

    assert policy_imports == {("reservations.domain", 2, "reservation_attempt_result_policy", None)}


def _legacy_service_reservation_attempt_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    legacy_owner = ("rail_waitlist", "services")
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []
    package_aliases: set[str] = set()
    service_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = tuple(alias.name.split("."))
                if imported == ("rail_waitlist",):
                    package_aliases.add(alias.asname or "rail_waitlist")
                if imported == legacy_owner:
                    if alias.asname is None:
                        package_aliases.add("rail_waitlist")
                    else:
                        service_aliases.add(alias.asname)
                if imported == ("importlib",):
                    importlib_aliases.add(alias.asname or "importlib")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        imported_names = {alias.name for alias in node.names}
        if resolved == legacy_owner and (
            imported_names & LEGACY_SERVICE_RESERVATION_ATTEMPT_SYMBOLS or "*" in imported_names
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == ("rail_waitlist",):
            service_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "services"
            )
        if resolved == ("importlib",):
            import_module_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if value is None:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(value, ast.Name) and value.id in service_aliases:
                    before = len(service_aliases)
                    service_aliases.add(target.id)
                    changed = changed or len(service_aliases) != before
                if isinstance(value, ast.Name) and value.id in package_aliases:
                    before = len(package_aliases)
                    package_aliases.add(target.id)
                    changed = changed or len(package_aliases) != before
                parts = _attribute_chain(value)
                if len(parts) == 2 and parts[0] in package_aliases and parts[1] == "services":
                    before = len(service_aliases)
                    service_aliases.add(target.id)
                    changed = changed or len(service_aliases) != before

    def is_service_reference(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in service_aliases
        parts = _attribute_chain(node)
        return len(parts) == 2 and parts[0] in package_aliases and parts[1] == "services"

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = _attribute_chain(node)
            if (
                len(parts) >= 2
                and parts[0] in service_aliases
                and parts[-1] in LEGACY_SERVICE_RESERVATION_ATTEMPT_SYMBOLS
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> service-attribute")
            if (
                len(parts) >= 3
                and parts[0] in package_aliases
                and parts[1] == "services"
                and parts[-1] in LEGACY_SERVICE_RESERVATION_ATTEMPT_SYMBOLS
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-attribute")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and is_service_reference(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in LEGACY_SERVICE_RESERVATION_ATTEMPT_SYMBOLS
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> getattr")
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.services"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> __import__")
        dynamic_import = (
            isinstance(node.func, ast.Name) and node.func.id in import_module_aliases
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in importlib_aliases
            and node.func.attr == "import_module"
        )
        if (
            dynamic_import
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.services"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> importlib")

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from .services import begin_reservation_attempt",
        "from rail_waitlist.services import complete_reservation_attempt",
        "from rail_waitlist.services import *",
        "import rail_waitlist.services as legacy; legacy.begin_reservation_attempt",
        "import rail_waitlist; rail_waitlist.services.complete_reservation_attempt",
        (
            "from rail_waitlist import services as legacy; "
            "getattr(legacy, 'reservation_attempt_result_policy')"
        ),
        (
            "import rail_waitlist.services as legacy; alias = legacy; "
            "alias.complete_reservation_attempt"
        ),
        "import importlib; importlib.import_module('rail_waitlist.services')",
        "from importlib import import_module as load; load('rail_waitlist.services')",
        "__import__('rail_waitlist.services')",
    ],
)
def test_legacy_service_reservation_attempt_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _legacy_service_reservation_attempt_references(
        source,
        Path("rail_waitlist/probe.py"),
    )


def test_production_does_not_reenter_legacy_services_for_reservation_attempts() -> None:
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == "rail_waitlist/services.py":
            continue
        violations.extend(
            _legacy_service_reservation_attempt_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_worker_does_not_import_the_legacy_service_hub() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "worker.py"
    relative_path = module_path.relative_to(SOURCE_ROOT)
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    legacy_imports: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "rail_waitlist.services" for alias in node.names):
                legacy_imports.append(node.lineno)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        if resolved == ("rail_waitlist", "services") or (
            resolved == ("rail_waitlist",) and any(alias.name == "services" for alias in node.names)
        ):
            legacy_imports.append(node.lineno)

    assert legacy_imports == []


def test_provider_adapters_only_depend_on_provider_registry_contract_leaves() -> None:
    violations: list[str] = []
    adapter_root = SOURCE_ROOT / "rail_waitlist" / "provider_adapters"
    allowed_registry_leaves = {
        ("rail_waitlist", "provider_registry", "contracts"),
        ("rail_waitlist", "provider_registry", "korail_search_contracts"),
    }

    for module_path in sorted(adapter_root.glob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("rail_waitlist.provider_registry"):
                        violations.append(
                            f"{relative_path.as_posix()}:{node.lineno} -> {alias.name}"
                        )
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                module_parts = list(relative_path.with_suffix("").parts)
                package_parts = module_parts[:-1]
                keep = len(package_parts) - (node.level - 1)
                imported_parts = tuple(part for part in (node.module or "").split(".") if part)
                resolved = (*package_parts[:keep], *imported_parts)
            if (
                resolved[:2] == ("rail_waitlist", "provider_registry")
                and resolved not in allowed_registry_leaves
            ):
                violations.append(
                    f"{relative_path.as_posix()}:{node.lineno} -> {'.'.join(resolved)}"
                )

    assert violations == []


WATCH_COMMAND_RUNTIME_SYMBOLS = {"create_watch", "update_watch"}


def test_watch_command_runtime_has_an_exact_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "command_runtime.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    direct_import_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "UTC", None),
        ("datetime", 0, "datetime", None),
        ("sqlalchemy.ext.asyncio", 0, "AsyncSession", None),
        ("idempotency.application", 2, "get_idempotent_resource", None),
        ("idempotency.application", 2, "remember_idempotency", None),
        ("idempotency.application", 2, "request_hash", None),
        ("outbox", 2, "add_outbox_event", None),
        ("policy", 2, "build_watch_dedupe_key", None),
        ("provider_registry.application", 2, "get_timetable_provider", None),
        ("config", 2, "get_settings", None),
        ("create_application", 1, "WatchCreateDependencies", None),
        ("create_application", 1, "create_watch", "create_watch_application"),
        ("models", 1, "Watch", None),
        ("schemas", 1, "WatchCreate", None),
        ("schemas", 1, "WatchUpdate", None),
        ("update_application", 1, "WatchUpdateDependencies", None),
        ("update_application", 1, "ensure_focused_observation_capacity", None),
        ("update_application", 1, "update_watch", "update_watch_application"),
        ("update_application", 1, "validate_channel_ids", None),
    }
    assert direct_import_roots == set()
    assert all(
        (node.module or "").split(".")[0] != "fastapi"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert called_attributes.isdisjoint(
        {
            "add",
            "begin",
            "begin_nested",
            "commit",
            "delete",
            "flush",
            "refresh",
            "rollback",
            "with_for_update",
        }
    )


def test_watch_command_runtime_has_the_exact_production_consumer() -> None:
    owner = ("rail_waitlist", "watch_management", "command_runtime")
    direct_consumers: dict[str, set[str]] = {}

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == "rail_waitlist/watch_management/command_runtime.py":
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                imported_parts = tuple(part for part in (node.module or "").split(".") if part)
                resolved = (*package_parts[:keep], *imported_parts)
            if resolved == owner:
                imports.update(
                    alias.name
                    for alias in node.names
                    if alias.name in WATCH_COMMAND_RUNTIME_SYMBOLS
                )
        if imports:
            direct_consumers[relative_path.as_posix()] = imports

    assert direct_consumers == {
        "rail_waitlist/watch_management/http.py": WATCH_COMMAND_RUNTIME_SYMBOLS,
    }


def _legacy_service_hub_references(source: str, relative_path: Path) -> list[str]:
    legacy_owner = ("rail_waitlist", "services")
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []
    package_aliases: set[str] = set()
    service_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = tuple(alias.name.split("."))
                if imported == ("rail_waitlist",):
                    package_aliases.add(alias.asname or "rail_waitlist")
                if imported == legacy_owner:
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} -> import")
                    if alias.asname is None:
                        package_aliases.add("rail_waitlist")
                    else:
                        service_aliases.add(alias.asname)
                if imported == ("importlib",):
                    importlib_aliases.add(alias.asname or "importlib")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        if resolved == legacy_owner:
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> from-import")
        if resolved == ("rail_waitlist",):
            for alias in node.names:
                if alias.name == "services":
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-import")
                    service_aliases.add(alias.asname or alias.name)
        if resolved == ("importlib",):
            import_module_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if value is None:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(value, ast.Name) and value.id in service_aliases:
                    before = len(service_aliases)
                    service_aliases.add(target.id)
                    changed = changed or len(service_aliases) != before
                if isinstance(value, ast.Name) and value.id in package_aliases:
                    before = len(package_aliases)
                    package_aliases.add(target.id)
                    changed = changed or len(package_aliases) != before
                parts = _attribute_chain(value)
                if len(parts) == 2 and parts[0] in package_aliases and parts[1] == "services":
                    before = len(service_aliases)
                    service_aliases.add(target.id)
                    changed = changed or len(service_aliases) != before

    def is_service_reference(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in service_aliases
        parts = _attribute_chain(node)
        return len(parts) == 2 and parts[0] in package_aliases and parts[1] == "services"

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = _attribute_chain(node)
            if len(parts) >= 2 and parts[0] in service_aliases:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> service-attribute")
            if len(parts) >= 2 and parts[0] in package_aliases and parts[1] == "services":
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-attribute")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            attribute = node.args[1]
            if is_service_reference(node.args[0]) or (
                isinstance(node.args[0], ast.Name)
                and node.args[0].id in package_aliases
                and isinstance(attribute, ast.Constant)
                and attribute.value == "services"
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> getattr")
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.services"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> __import__")
        dynamic_import = (
            isinstance(node.func, ast.Name) and node.func.id in import_module_aliases
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in importlib_aliases
            and node.func.attr == "import_module"
        )
        if (
            dynamic_import
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.services"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> importlib")

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from .services import create_watch",
        "from rail_waitlist.services import update_watch",
        "from rail_waitlist.services import *",
        "import rail_waitlist.services",
        "import rail_waitlist.services as legacy",
        "import rail_waitlist; rail_waitlist.services.create_watch",
        "import rail_waitlist as rw; rw.services.update_watch",
        "from rail_waitlist import services as legacy; legacy.create_watch",
        "import rail_waitlist as rw; alias = rw; alias.services.create_watch",
        "import rail_waitlist; getattr(rail_waitlist, 'services')",
        "import importlib; importlib.import_module('rail_waitlist.services')",
        "from importlib import import_module as load; load('rail_waitlist.services')",
        "__import__('rail_waitlist.services')",
    ],
)
def test_legacy_service_hub_detector_rejects_all_access_forms(source: str) -> None:
    assert _legacy_service_hub_references(source, Path("rail_waitlist/probe.py"))


def test_watch_http_does_not_reenter_the_legacy_service_hub() -> None:
    relative_path = Path("rail_waitlist/watch_management/http.py")
    module_path = SOURCE_ROOT / relative_path

    assert (
        _legacy_service_hub_references(
            module_path.read_text(encoding="utf-8"),
            relative_path,
        )
        == []
    )


def test_production_does_not_reenter_the_legacy_service_hub() -> None:
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == "rail_waitlist/services.py":
            continue
        violations.extend(
            _legacy_service_hub_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


PROVIDER_FAILURE_POLICY_SYMBOLS = {
    "BLOCK_COOLDOWN",
    "ErrorPolicyResult",
    "PROTECTION_SIGNALS",
    "RATE_LIMIT_COOLDOWN",
    "classify_provider_failure",
    "cooldown_until",
}


def test_provider_failure_policy_owner_has_exact_imports_and_definitions() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "provider_failure_policy.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }

    assert direct_imports == set()
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "datetime", None),
        ("datetime", 0, "timedelta", None),
        ("datetime", 0, "timezone", None),
        ("domain", 2, "WatchStatus", None),
        ("schema_base", 2, "ApiModel", None),
    }
    assert classes == {"ErrorPolicyResult"}
    assert functions == {"classify_provider_failure", "cooldown_until"}
    assert assignments == {
        "BLOCK_COOLDOWN",
        "PROTECTION_SIGNALS",
        "RATE_LIMIT_COOLDOWN",
    }


def test_top_level_policy_is_an_exact_failure_facade_with_local_dedupe_and_cadence() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "policy.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    direct_imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }

    assert direct_imports == {("hashlib", None), ("json", None)}
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "date", None),
        ("datetime", 0, "datetime", None),
        ("datetime", 0, "time", None),
        ("datetime", 0, "timedelta", None),
        ("domain", 1, "Provider", None),
        ("domain", 1, "SeatObservationMode", None),
        *{
            ("watch_management.provider_failure_policy", 1, symbol, symbol)
            for symbol in PROVIDER_FAILURE_POLICY_SYMBOLS
        },
    }
    assert classes == set()
    assert functions == {"build_watch_dedupe_key", "next_interval"}
    assert assignments == {
        "OBSERVATION_INTERVAL_MAX_SECONDS",
        "OBSERVATION_INTERVAL_MIN_SECONDS",
    }


def test_central_schemas_exactly_aliases_provider_failure_result_without_a_class() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    module_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "watch_management"
        and node.level == 1
        for alias in node.names
        if alias.name == "provider_failure_policy"
    }
    local_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ErrorPolicyResult"
    }
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "ErrorPolicyResult"
    }

    assert module_imports == {
        (
            "watch_management",
            1,
            "provider_failure_policy",
            "watch_provider_failure_policy",
        )
    }
    assert local_classes == set()
    assert set(aliases) == {"ErrorPolicyResult"}
    value = aliases["ErrorPolicyResult"]
    assert isinstance(value, ast.Attribute)
    assert value.attr == "ErrorPolicyResult"
    assert isinstance(value.value, ast.Name)
    assert value.value.id == "watch_provider_failure_policy"


def _module_contract_references(
    source: str,
    relative_path: Path,
    *,
    owner: tuple[str, ...],
    symbols: set[str],
) -> list[str]:
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []
    package_aliases: set[str] = set()
    owner_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = tuple(alias.name.split("."))
                if imported == ("rail_waitlist",):
                    package_aliases.add(alias.asname or "rail_waitlist")
                if imported == owner:
                    if alias.asname is None:
                        package_aliases.add("rail_waitlist")
                    else:
                        owner_aliases.add(alias.asname)
                if imported == ("importlib",):
                    importlib_aliases.add(alias.asname or "importlib")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        imported_names = {alias.name for alias in node.names}
        if resolved == owner and (imported_names & symbols or "*" in imported_names):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == owner[:-1]:
            owner_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == owner[-1]
            )
        if resolved == ("importlib",):
            import_module_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if value is None:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(value, ast.Name) and value.id in owner_aliases:
                    before = len(owner_aliases)
                    owner_aliases.add(target.id)
                    changed = changed or len(owner_aliases) != before
                if isinstance(value, ast.Name) and value.id in package_aliases:
                    before = len(package_aliases)
                    package_aliases.add(target.id)
                    changed = changed or len(package_aliases) != before
                parts = _attribute_chain(value)
                if (
                    len(parts) == len(owner)
                    and parts[0] in package_aliases
                    and tuple(parts[1:]) == owner[1:]
                ):
                    before = len(owner_aliases)
                    owner_aliases.add(target.id)
                    changed = changed or len(owner_aliases) != before

    def is_owner_reference(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in owner_aliases
        parts = _attribute_chain(node)
        return (
            len(parts) == len(owner)
            and parts[0] in package_aliases
            and tuple(parts[1:]) == owner[1:]
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = _attribute_chain(node)
            if len(parts) >= 2 and parts[0] in owner_aliases and parts[-1] in symbols:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> owner-attribute")
            if (
                len(parts) == len(owner) + 1
                and parts[0] in package_aliases
                and tuple(parts[1:-1]) == owner[1:]
                and parts[-1] in symbols
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-attribute")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and is_owner_reference(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in symbols
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> getattr")
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == ".".join(owner)
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> __import__")
        dynamic_import = (
            isinstance(node.func, ast.Name) and node.func.id in import_module_aliases
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in importlib_aliases
            and node.func.attr == "import_module"
        )
        if (
            dynamic_import
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == ".".join(owner)
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> importlib")

    return violations


def _top_level_failure_policy_references(source: str, relative_path: Path) -> list[str]:
    return _module_contract_references(
        source,
        relative_path,
        owner=("rail_waitlist", "policy"),
        symbols=PROVIDER_FAILURE_POLICY_SYMBOLS,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .policy import ErrorPolicyResult",
        "from rail_waitlist.policy import classify_provider_failure",
        "from .policy import *",
        "import rail_waitlist.policy as p; p.cooldown_until",
        "from rail_waitlist import policy as p; p.PROTECTION_SIGNALS",
        "import rail_waitlist as rw; rw.policy.RATE_LIMIT_COOLDOWN",
        "import rail_waitlist.policy as p; alias = p; alias.BLOCK_COOLDOWN",
        "import rail_waitlist.policy as p; getattr(p, 'ErrorPolicyResult')",
        "import importlib; importlib.import_module('rail_waitlist.policy')",
        "from importlib import import_module as load; load('rail_waitlist.policy')",
        "__import__('rail_waitlist.policy')",
    ],
)
def test_top_level_failure_policy_detector_rejects_all_access_forms(source: str) -> None:
    assert _top_level_failure_policy_references(source, Path("rail_waitlist/probe.py"))


def test_top_level_failure_policy_detector_allows_retained_policy_symbols() -> None:
    source = (
        "from .policy import build_watch_dedupe_key, next_interval; "
        "import rail_waitlist.policy as p; p.OBSERVATION_INTERVAL_MAX_SECONDS"
    )
    assert _top_level_failure_policy_references(source, Path("rail_waitlist/probe.py")) == []


def test_production_does_not_reenter_top_level_policy_for_moved_failure_symbols() -> None:
    violations: list[str] = []
    legacy_path = "rail_waitlist/policy.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == legacy_path:
            continue
        violations.extend(
            _top_level_failure_policy_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def _central_schema_failure_result_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    return _central_schema_contract_references(
        source,
        relative_path,
        {"ErrorPolicyResult"},
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import ErrorPolicyResult",
        "from rail_waitlist.schemas import ErrorPolicyResult",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.ErrorPolicyResult",
        "from rail_waitlist import schemas as s; s.ErrorPolicyResult",
        "import rail_waitlist as rw; rw.schemas.ErrorPolicyResult",
        "import rail_waitlist.schemas as s; alias = s; alias.ErrorPolicyResult",
        "import rail_waitlist.schemas as s; getattr(s, 'ErrorPolicyResult')",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module as load; load('rail_waitlist.schemas')",
        "__import__('rail_waitlist.schemas').schemas.ErrorPolicyResult",
    ],
)
def test_central_schema_failure_result_detector_rejects_all_access_forms(source: str) -> None:
    assert _central_schema_failure_result_references(source, Path("rail_waitlist/probe.py"))


def test_central_schema_failure_result_detector_allows_canonical_owner() -> None:
    source = (
        "from .schemas import HealthResponse; "
        "from rail_waitlist.watch_management.provider_failure_policy import ErrorPolicyResult"
    )
    assert _central_schema_failure_result_references(source, Path("rail_waitlist/probe.py")) == []


def test_production_does_not_reenter_central_schemas_for_failure_result() -> None:
    violations: list[str] = []
    central_path = "rail_waitlist/schemas.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _central_schema_failure_result_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_provider_failure_policy_has_no_operational_consumers() -> None:
    owner = ("rail_waitlist", "watch_management", "provider_failure_policy")
    violations: list[str] = []
    excluded_paths = {
        "rail_waitlist/policy.py",
        "rail_waitlist/schemas.py",
        "rail_waitlist/watch_management/provider_failure_policy.py",
    }

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in excluded_paths:
            continue
        violations.extend(
            _module_contract_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
                owner=owner,
                symbols=PROVIDER_FAILURE_POLICY_SYMBOLS,
            )
        )

    assert violations == []


def test_health_schema_owner_has_an_exact_definition_and_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "health" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    classes = {
        node.name: [base.id for base in node.bases if isinstance(base, ast.Name)]
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("schema_base", 2, "ApiModel", None),
    }
    assert classes == {"HealthResponse": ["ApiModel"]}


def test_central_schema_is_a_classless_health_compatibility_facade() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    class_definitions = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    health_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "health" and node.level == 1
        for alias in node.names
    }
    health_aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "HealthResponse"
    }

    assert class_definitions == set()
    assert health_imports == {("health", 1, "schemas", "health_schemas")}
    assert set(health_aliases) == {"HealthResponse"}
    health_value = health_aliases["HealthResponse"]
    assert isinstance(health_value, ast.Attribute)
    assert isinstance(health_value.value, ast.Name)
    assert health_value.value.id == "health_schemas"
    assert health_value.attr == "HealthResponse"


def test_health_schema_has_one_exact_canonical_production_consumer() -> None:
    owner = ("rail_waitlist", "health", "schemas")
    direct_consumers: dict[str, set[tuple[str, str | None]]] = {}
    canonical_consumers: set[str] = set()

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in {
            "rail_waitlist/health/schemas.py",
            "rail_waitlist/schemas.py",
        }:
            continue
        module_parts = list(relative_path.with_suffix("").parts)
        package_parts = module_parts[:-1]
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        if _module_contract_references(
            module_path.read_text(encoding="utf-8"),
            relative_path,
            owner=owner,
            symbols={"HealthResponse"},
        ):
            canonical_consumers.add(relative_path.as_posix())
        imports: set[tuple[str, str | None]] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                resolved = tuple((node.module or "").split("."))
            else:
                keep = len(package_parts) - (node.level - 1)
                imported_parts = tuple(part for part in (node.module or "").split(".") if part)
                resolved = (*package_parts[:keep], *imported_parts)
            if resolved == owner:
                imports.update(
                    (alias.name, alias.asname)
                    for alias in node.names
                    if alias.name == "HealthResponse"
                )
        if imports:
            direct_consumers[relative_path.as_posix()] = imports

    assert direct_consumers == {
        "rail_waitlist/main.py": {("HealthResponse", None)},
    }
    assert canonical_consumers == {"rail_waitlist/main.py"}


def _central_schema_health_response_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    return _central_schema_contract_references(
        source,
        relative_path,
        {"HealthResponse"},
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import HealthResponse",
        "from rail_waitlist.schemas import HealthResponse",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.HealthResponse",
        "from rail_waitlist import schemas as s; s.HealthResponse",
        "import rail_waitlist as rw; rw.schemas.HealthResponse",
        "import rail_waitlist.schemas as s; alias = s; alias.HealthResponse",
        "import rail_waitlist.schemas as s; getattr(s, 'HealthResponse')",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module as load; load('rail_waitlist.schemas')",
        "__import__('rail_waitlist.schemas').schemas.HealthResponse",
        "import rail_waitlist as rw; s = rw.schemas; s.HealthResponse",
    ],
)
def test_central_schema_health_response_detector_rejects_all_access_forms(source: str) -> None:
    assert _central_schema_health_response_references(source, Path("rail_waitlist/probe.py"))


def test_central_schema_health_response_detector_allows_the_canonical_owner() -> None:
    source = (
        "from rail_waitlist.health.schemas import HealthResponse; "
        "from rail_waitlist.schemas import ErrorPolicyResult"
    )

    assert _central_schema_health_response_references(source, Path("rail_waitlist/probe.py")) == []


def test_production_does_not_reenter_central_schemas_for_health_response() -> None:
    violations: list[str] = []
    central_path = "rail_waitlist/schemas.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _central_schema_health_response_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_health_package_remains_a_passive_namespace() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "health" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


KORAIL_SEARCH_BOOTSTRAP_SYMBOLS = {
    "OFFICIAL_KORAIL_STATION_DATA_URL",
    "OFFICIAL_KORAIL_RESULT_URL",
    "MIN_STATION_COUNT",
    "MAX_STATION_COUNT",
    "STATION_REQUEST_TIMEOUT",
    "KorailStationIdentityUnavailable",
    "KorailStationIdentity",
    "KorailStationIdentityCatalog",
    "KorailStationIdentityResolver",
    "parse_korail_station_identities",
    "build_korail_general_search_url",
    "validate_korail_general_search_url",
}


def test_korail_search_bootstrap_owner_has_an_exact_definition_and_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_adapters" / "korail_search_bootstrap.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("collections.abc", 0, "Callable", None),
        ("config", 2, "OFFICIAL_KORAIL_STATION_DATA_URL", "OFFICIAL_KORAIL_STATION_DATA_URL"),
        ("dataclasses", 0, "dataclass", None),
        (
            "provider_registry.korail_search_contracts",
            2,
            "KorailStationIdentity",
            "KorailStationIdentity",
        ),
    }
    assert definitions == {
        "KorailStationIdentityUnavailable",
        "KorailStationIdentityCatalog",
        "KorailStationIdentityResolver",
        "_normalize_station_name",
        "parse_korail_station_identities",
    }


def test_korail_search_contract_has_an_exact_definition_and_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_registry" / "korail_search_contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("dataclasses", 0, "dataclass", None),
    }
    assert definitions == {"KorailStationIdentity"}


def test_korail_search_url_policy_has_an_exact_definition_and_import_boundary() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "provider_registry" / "korail_search_url_policy.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "date", None),
        ("datetime", 0, "time", "clock_time"),
        ("korail_search_contracts", 1, "KorailStationIdentity", None),
        ("urllib.parse", 0, "parse_qsl", None),
        ("urllib.parse", 0, "urlencode", None),
        ("urllib.parse", 0, "urlsplit", None),
    }
    assert definitions == {
        "build_korail_general_search_url",
        "validate_korail_general_search_url",
    }


def test_top_level_korail_search_bootstrap_is_an_exact_alias_facade() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_search_bootstrap.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in KORAIL_SEARCH_BOOTSTRAP_SYMBOLS
    }

    assert definitions == set()
    assert imports == {
        ("provider_adapters", 1, "korail_search_bootstrap", "_bootstrap"),
        ("provider_registry", 1, "korail_search_contracts", "_contracts"),
        ("provider_registry", 1, "korail_search_url_policy", "_url_policy"),
    }
    assert set(aliases) == KORAIL_SEARCH_BOOTSTRAP_SYMBOLS
    expected_owner = {
        "OFFICIAL_KORAIL_STATION_DATA_URL": "_bootstrap",
        "OFFICIAL_KORAIL_RESULT_URL": "_url_policy",
        "MIN_STATION_COUNT": "_bootstrap",
        "MAX_STATION_COUNT": "_bootstrap",
        "STATION_REQUEST_TIMEOUT": "_bootstrap",
        "KorailStationIdentityUnavailable": "_bootstrap",
        "KorailStationIdentity": "_contracts",
        "KorailStationIdentityCatalog": "_bootstrap",
        "KorailStationIdentityResolver": "_bootstrap",
        "parse_korail_station_identities": "_bootstrap",
        "build_korail_general_search_url": "_url_policy",
        "validate_korail_general_search_url": "_url_policy",
    }
    for symbol, value in aliases.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == expected_owner[symbol]
        assert value.attr == symbol


def _legacy_korail_search_bootstrap_references(
    source: str,
    relative_path: Path,
) -> list[str]:
    return _module_contract_references(
        source,
        relative_path,
        owner=("rail_waitlist", "korail_search_bootstrap"),
        symbols=KORAIL_SEARCH_BOOTSTRAP_SYMBOLS,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .korail_search_bootstrap import KorailStationIdentityResolver",
        "from rail_waitlist.korail_search_bootstrap import KorailStationIdentityResolver",
        "from .korail_search_bootstrap import *",
        (
            "import rail_waitlist.korail_search_bootstrap as bootstrap; "
            "bootstrap.KorailStationIdentityResolver"
        ),
        (
            "from rail_waitlist import korail_search_bootstrap as bootstrap; "
            "bootstrap.KorailStationIdentityResolver"
        ),
        "import rail_waitlist as rw; rw.korail_search_bootstrap.KorailStationIdentityResolver",
        (
            "import rail_waitlist.korail_search_bootstrap as bootstrap; "
            "getattr(bootstrap, 'KorailStationIdentityResolver')"
        ),
        "import importlib; importlib.import_module('rail_waitlist.korail_search_bootstrap')",
        (
            "from importlib import import_module as load; "
            "load('rail_waitlist.korail_search_bootstrap')"
        ),
        (
            "__import__('rail_waitlist.korail_search_bootstrap')."
            "korail_search_bootstrap.KorailStationIdentityResolver"
        ),
    ],
)
def test_legacy_korail_search_bootstrap_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _legacy_korail_search_bootstrap_references(source, Path("rail_waitlist/probe.py"))


def test_production_does_not_reenter_legacy_korail_search_bootstrap() -> None:
    violations: list[str] = []
    facade_path = "rail_waitlist/korail_search_bootstrap.py"

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == facade_path:
            continue
        violations.extend(
            _legacy_korail_search_bootstrap_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_korail_search_bootstrap_has_the_exact_canonical_production_consumers() -> None:
    owner = ("rail_waitlist", "provider_adapters", "korail_search_bootstrap")
    consumers: set[str] = set()
    excluded_paths = {
        "rail_waitlist/korail_search_bootstrap.py",
        "rail_waitlist/provider_adapters/korail_search_bootstrap.py",
    }

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in excluded_paths:
            continue
        if _module_contract_references(
            module_path.read_text(encoding="utf-8"),
            relative_path,
            owner=owner,
            symbols=KORAIL_SEARCH_BOOTSTRAP_SYMBOLS,
        ):
            consumers.add(relative_path.as_posix())

    assert consumers == {
        "rail_waitlist/korail_browser_mode_smoke.py",
        "rail_waitlist/korail_pydoll_browser.py",
        "rail_waitlist/korail_sidecar/pydoll/search_actor.py",
        "rail_waitlist/korail_sidecar/runtime.py",
    }


def test_korail_search_url_policy_has_the_exact_canonical_production_consumers() -> None:
    owner = ("rail_waitlist", "provider_registry", "korail_search_url_policy")
    consumers: set[str] = set()
    excluded_paths = {
        "rail_waitlist/korail_search_bootstrap.py",
        "rail_waitlist/provider_registry/korail_search_url_policy.py",
    }

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in excluded_paths:
            continue
        if _module_contract_references(
            module_path.read_text(encoding="utf-8"),
            relative_path,
            owner=owner,
            symbols=KORAIL_SEARCH_BOOTSTRAP_SYMBOLS,
        ):
            consumers.add(relative_path.as_posix())

    assert consumers == {
        "rail_waitlist/korail_browser_automation.py",
        "rail_waitlist/korail_pydoll_browser.py",
        "rail_waitlist/korail_sidecar/browser_contracts.py",
        "rail_waitlist/korail_sidecar/pydoll/search_actor.py",
        "rail_waitlist/korail_sidecar/playwright/client.py",
        "rail_waitlist/schemas.py",
        "rail_waitlist/timetable_management/schemas.py",
    }


def test_korail_search_contract_has_the_exact_canonical_production_consumers() -> None:
    owner = ("rail_waitlist", "provider_registry", "korail_search_contracts")
    consumers: set[str] = set()
    excluded_paths = {
        "rail_waitlist/korail_search_bootstrap.py",
        "rail_waitlist/provider_registry/korail_search_contracts.py",
    }

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in excluded_paths:
            continue
        if _module_contract_references(
            module_path.read_text(encoding="utf-8"),
            relative_path,
            owner=owner,
            symbols={"KorailStationIdentity"},
        ):
            consumers.add(relative_path.as_posix())

    assert consumers == {
        "rail_waitlist/provider_adapters/korail_search_bootstrap.py",
        "rail_waitlist/provider_registry/korail_search_url_policy.py",
    }


DIRECT_CDP_COMPATIBILITY_SYMBOLS = {
    "AsyncIterator",
    "ChromiumBrowserType",
    "DirectCdpLaunchError",
    "Path",
    "Protocol",
    "_chromium_environment",
    "_cleanup_browser_process",
    "_stop_process",
    "_wait_for_debugging_port",
    "asynccontextmanager",
    "asyncio",
    "isolated_test_chromium_arguments",
    "logger",
    "logging",
    "open_direct_cdp_browser",
    "os",
    "tempfile",
    "time",
}
CHROMIUM_LAUNCH_COMPATIBILITY_SYMBOLS = {
    "_TEST_DISABLE_SANDBOX_ENV",
    "isolated_test_chromium_arguments",
    "os",
}


def test_direct_cdp_owner_has_the_exact_definition_and_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "direct_cdp.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert definitions == {
        "_BrowserCdpSession",
        "_DirectCdpBrowser",
        "ChromiumBrowserType",
        "DirectCdpLaunchError",
        "open_direct_cdp_browser",
        "_chromium_environment",
        "_cleanup_browser_process",
        "_wait_for_debugging_port",
        "_stop_process",
    }
    assert imports == {
        ("asyncio", None),
        ("logging", None),
        ("os", None),
        ("tempfile", None),
        ("time", None),
    }
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("chromium_launch", 1, "isolated_test_chromium_arguments", None),
        ("collections.abc", 0, "AsyncIterator", None),
        ("contextlib", 0, "asynccontextmanager", None),
        ("pathlib", 0, "Path", None),
        ("typing", 0, "Protocol", None),
        ("typing", 0, "TypeVar", None),
    }


def test_chromium_launch_owner_has_the_exact_definition_and_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "chromium_launch.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == {"isolated_test_chromium_arguments"}
    assert imports == {("os", None)}
    assert imports_from == {("__future__", 0, "annotations", None)}


@pytest.mark.parametrize(
    ("relative_path", "owner_name", "symbols"),
    [
        (
            "rail_waitlist/korail_direct_cdp.py",
            "direct_cdp",
            DIRECT_CDP_COMPATIBILITY_SYMBOLS,
        ),
        (
            "rail_waitlist/korail_chromium_launch.py",
            "chromium_launch",
            CHROMIUM_LAUNCH_COMPATIBILITY_SYMBOLS,
        ),
    ],
)
def test_legacy_browser_lifecycle_modules_are_exact_alias_facades(
    relative_path: str,
    owner_name: str,
    symbols: set[str],
) -> None:
    module_path = SOURCE_ROOT / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name != "annotations"
    }
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in symbols
    }

    assert definitions == set()
    assert imports == {("korail_sidecar", 1, owner_name, "_owner")}
    assert set(aliases) == symbols
    for symbol, value in aliases.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol


def _legacy_browser_lifecycle_references(
    source: str,
    relative_path: Path,
    *,
    owner_name: str,
    symbols: set[str],
) -> list[str]:
    return _module_contract_references(
        source,
        relative_path,
        owner=("rail_waitlist", owner_name),
        symbols=symbols,
    )


@pytest.mark.parametrize(
    ("owner_name", "symbol"),
    [
        ("korail_direct_cdp", "DirectCdpLaunchError"),
        ("korail_chromium_launch", "isolated_test_chromium_arguments"),
    ],
)
@pytest.mark.parametrize(
    "source_template",
    [
        "from .{owner} import {symbol}",
        "from rail_waitlist.{owner} import {symbol}",
        "from .{owner} import *",
        "import rail_waitlist.{owner} as owner; owner.{symbol}",
        "from rail_waitlist import {owner} as owner; owner.{symbol}",
        "import rail_waitlist as rw; rw.{owner}.{symbol}",
        "import rail_waitlist.{owner} as owner; alias = owner; alias.{symbol}",
        "import rail_waitlist.{owner} as owner; getattr(owner, '{symbol}')",
        "import importlib; importlib.import_module('rail_waitlist.{owner}')",
        "from importlib import import_module as load; load('rail_waitlist.{owner}')",
        "__import__('rail_waitlist.{owner}').{owner}.{symbol}",
    ],
)
def test_legacy_browser_lifecycle_detector_rejects_all_access_forms(
    owner_name: str,
    symbol: str,
    source_template: str,
) -> None:
    source = source_template.format(owner=owner_name, symbol=symbol)
    symbols = (
        DIRECT_CDP_COMPATIBILITY_SYMBOLS
        if owner_name == "korail_direct_cdp"
        else CHROMIUM_LAUNCH_COMPATIBILITY_SYMBOLS
    )

    assert _legacy_browser_lifecycle_references(
        source,
        Path("rail_waitlist/probe.py"),
        owner_name=owner_name,
        symbols=symbols,
    )


@pytest.mark.parametrize(
    ("owner_name", "symbols", "facade_path"),
    [
        (
            "korail_direct_cdp",
            DIRECT_CDP_COMPATIBILITY_SYMBOLS,
            "rail_waitlist/korail_direct_cdp.py",
        ),
        (
            "korail_chromium_launch",
            CHROMIUM_LAUNCH_COMPATIBILITY_SYMBOLS,
            "rail_waitlist/korail_chromium_launch.py",
        ),
    ],
)
def test_production_does_not_reenter_legacy_browser_lifecycle_modules(
    owner_name: str,
    symbols: set[str],
    facade_path: str,
) -> None:
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == facade_path:
            continue
        violations.extend(
            _legacy_browser_lifecycle_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
                owner_name=owner_name,
                symbols=symbols,
            )
        )

    assert violations == []


@pytest.mark.parametrize(
    ("owner", "symbols", "excluded_paths", "expected_consumers"),
    [
        (
            ("rail_waitlist", "korail_sidecar", "direct_cdp"),
            DIRECT_CDP_COMPATIBILITY_SYMBOLS,
            {
                "rail_waitlist/korail_direct_cdp.py",
                "rail_waitlist/korail_sidecar/direct_cdp.py",
            },
            {
                "rail_waitlist/korail_browser_automation.py",
                "rail_waitlist/korail_sidecar/playwright/client.py",
            },
        ),
        (
            ("rail_waitlist", "korail_sidecar", "chromium_launch"),
            CHROMIUM_LAUNCH_COMPATIBILITY_SYMBOLS,
            {
                "rail_waitlist/korail_chromium_launch.py",
                "rail_waitlist/korail_sidecar/chromium_launch.py",
            },
            {
                "rail_waitlist/korail_sidecar/direct_cdp.py",
                "rail_waitlist/korail_sidecar/pydoll/chromium_lifecycle.py",
            },
        ),
    ],
)
def test_browser_lifecycle_owner_has_exact_canonical_production_consumers(
    owner: tuple[str, ...],
    symbols: set[str],
    excluded_paths: set[str],
    expected_consumers: set[str],
) -> None:
    consumers: set[str] = set()

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in excluded_paths:
            continue
        if _module_contract_references(
            module_path.read_text(encoding="utf-8"),
            relative_path,
            owner=owner,
            symbols=symbols,
        ):
            consumers.add(relative_path.as_posix())

    assert consumers == expected_consumers


def test_korail_sidecar_package_remains_a_passive_namespace() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


SRT_WIRE_COMPATIBILITY_SYMBOLS = {
    "BaseModel",
    "ConfigDict",
    "Field",
    "KOREA",
    "Literal",
    "Provider",
    "ProviderCredentials",
    "ReservationConfirmationOutcome",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
    "ReservationRequest",
    "ReservationResult",
    "SeatClass",
    "SeatObservationRequest",
    "SeatObservationResult",
    "SecretStr",
    "SrtConfirmReservationRequest",
    "SrtConfirmReservationResult",
    "SrtCredentialRequest",
    "SrtLoginRequest",
    "SrtLoginResult",
    "SrtObserveRequest",
    "SrtObserveResult",
    "SrtOfficialSeatStatus",
    "SrtProviderAdapterModel",
    "SrtReservationConfirmationResult",
    "SrtReservationConfirmationTarget",
    "SrtReserveOnceRequest",
    "SrtReserveOnceResult",
    "SrtSessionActorState",
    "SrtSessionStatus",
    "SrtTimetableOverlayRequest",
    "SrtTimetableOverlayResult",
    "SrtTimetableSearchRequest",
    "SrtTimetableSearchResult",
    "SrtTimetableTrain",
    "TimetableItem",
    "ZoneInfo",
    "datetime",
    "model_validator",
}
SRT_CLIENT_COMPATIBILITY_SYMBOLS = {
    "ProviderCredentials",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
    "ReservationRequest",
    "ReservationResult",
    "SRTError",
    "SRTNetFunnelError",
    "SRT_PROVIDER_ADAPTER_ORIGIN",
    "SeatObservationRequest",
    "SeatObservationResult",
    "SrtConfirmReservationRequest",
    "SrtConfirmReservationResult",
    "SrtCredentialRequest",
    "SrtLoginRequest",
    "SrtLoginResult",
    "SrtObserveRequest",
    "SrtObserveResult",
    "SrtProviderAdapterClient",
    "SrtProviderAdapterUnavailable",
    "SrtReservationConfirmationTarget",
    "SrtReserveOnceRequest",
    "SrtReserveOnceResult",
    "SrtSessionStatus",
    "SrtTimetableOverlayRequest",
    "SrtTimetableOverlayResult",
    "SrtTimetableSearchRequest",
    "SrtTimetableSearchResult",
    "SrtTimetableTrain",
    "TimetableItem",
    "ValidationError",
    "datetime",
    "httpx",
    "urlsplit",
    "validate_srt_provider_adapter_url",
}
SRT_RESERVATION_COMPATIBILITY_SYMBOLS = {
    "Adult",
    "Callable",
    "KOREA",
    "Literal",
    "Lock",
    "Protocol",
    "RequestException",
    "ReservationConfirmationOutcome",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
    "ReservationOutcome",
    "ReservationRequest",
    "ReservationResult",
    "SRT",
    "SRTError",
    "SRTLoginError",
    "SRTNetFunnelError",
    "SRTNotLoggedInError",
    "SRTResponseError",
    "SRT_RESERVATION_HANDOFF_URL",
    "SRT_RESERVATION_LIST_SOURCE",
    "SRT_RESERVATION_SOURCE",
    "SeatClass",
    "SeatType",
    "SrtClientFactory",
    "SrtReservationCredentials",
    "SrtReservationExecutor",
    "SrtReservationListEvidence",
    "SrtReservationRecord",
    "SrtSessionActorSnapshot",
    "SrtSessionActorState",
    "SrtStationRosterUnavailable",
    "StrEnum",
    "ZoneInfo",
    "asyncio",
    "dataclass",
    "datetime",
    "default_srt_reservation_executor",
    "field",
    "hashlib",
    "load_srt_station_roster",
    "normalize_srt_date",
    "normalize_srt_reservation_records",
    "normalize_srt_time",
    "normalize_srt_train_number",
    "re",
    "time",
    "verify_srt_credentials_once",
}
KORAIL_BROWSER_CLIENT_SYMBOLS = {
    "BrowserAdapterTransport",
    "HttpBrowserAdapterTransport",
    "_AdapterFailure",
}
KORAIL_BROWSER_PROJECTION_SYMBOLS = {
    "_seat_class",
    "mark_not_observed",
    "normalize_train_number",
    "overlay_item",
    "project_overlay_items",
    "project_primary_timetable",
}
KORAIL_BROWSER_QUERY_RUNTIME_SYMBOLS = {
    "KorailBrowserQueryRuntime",
    "SOURCE_FAILURE_COOLDOWN_MAX_SECONDS",
    "_CacheEntry",
    "_ProviderCooldown",
    "_QueryCooldown",
}
KORAIL_SIDECAR_CONFIRMATION_RUNTIME_SYMBOLS = {
    "confirm_korail_sidecar_reservation",
}
TAGO_TIMETABLE_PROJECTION_SYMBOLS = {
    "project_tago_timetable_rows",
}
KORAIL_SEAT_SOURCE_COMPATIBILITY_SYMBOLS = {
    "AdultPassenger",
    "Callable",
    "CooldownStore",
    "HTTPAdapter",
    "KOREA",
    "Korail",
    "KorailClientFactory",
    "KorailError",
    "KorailLiveSeatSource",
    "KorailSeatSnapshot",
    "MemoryCooldownStore",
    "NoResultsError",
    "PROTECTION_MARKERS",
    "PassengerFactory",
    "Protocol",
    "RequestException",
    "SOURCE_NAME",
    "SeatAvailabilityAction",
    "SeatAvailabilityNotObservedReason",
    "SeatAvailabilityProvenance",
    "SeatAvailabilityStatus",
    "SeatClassAvailability",
    "TimetableItem",
    "UTC",
    "ZoneInfo",
    "asyncio",
    "dataclass",
    "datetime",
    "map_korail_seat_state",
    "normalize_date",
    "normalize_time",
    "normalize_train_number",
    "time",
}
KORAIL_SEAT_SOURCE_PRIVATE_COMPATIBILITY_SYMBOLS = {
    "_CacheEntry",
    "_DefaultTimeoutAdapter",
    "_KorailClient",
    "_KorailTrain",
    "_ProviderCooldown",
    "_default_client_factory",
}
SRT_SEAT_SOURCE_COMPATIBILITY_SYMBOLS = {
    "Callable",
    "CooldownStore",
    "KOREA",
    "MemoryCooldownStore",
    "ObservationErrorCategory",
    "Protocol",
    "Provider",
    "RequestException",
    "SOURCE_NAME",
    "SRTError",
    "SRTNetFunnelError",
    "SeatAvailabilityAction",
    "SeatAvailabilityNotObservedReason",
    "SeatAvailabilityProvenance",
    "SeatAvailabilityStatus",
    "SeatClass",
    "SeatClassAvailability",
    "SeatObservationRequest",
    "SeatObservationResult",
    "SrtClientFactory",
    "SrtLiveSeatSource",
    "SrtLiveTimetableUnavailable",
    "SrtOfficialTimetableTrain",
    "SrtSeatSnapshot",
    "SrtStationRosterUnavailable",
    "TimetableItem",
    "UTC",
    "ZoneInfo",
    "asyncio",
    "dataclass",
    "datetime",
    "load_srt_station_roster",
    "map_srt_seat_state",
    "normalize_srt_date",
    "normalize_srt_time",
    "normalize_srt_train_number",
    "time",
    "timedelta",
}
SRT_SEAT_SOURCE_PRIVATE_COMPATIBILITY_SYMBOLS = {
    "_AccountlessSrtClient",
    "_CacheEntry",
    "_ProviderCooldown",
    "_SrTrainCodeAwareClient",
    "_SrtClient",
    "_SrtTrain",
    "_default_client_factory",
    "_official_datetime",
    "_optional_date",
    "_optional_nonnegative_int",
    "_optional_text",
    "_optional_time",
    "_snapshot_station_name",
}


def test_provider_account_contract_is_a_stdlib_only_leaf() -> None:
    path = SOURCE_ROOT / "rail_waitlist" / "provider_account_management" / "contracts.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    definitions = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("dataclasses", 0, "dataclass", None),
        ("dataclasses", 0, "field", None),
        ("typing", 0, "Literal", None),
    }
    assert definitions == {"ProviderCredentials"}


def test_srt_session_contract_is_a_stdlib_only_leaf() -> None:
    path = SOURCE_ROOT / "rail_waitlist" / "srt_sidecar" / "session_contract.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    definitions = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("dataclasses", 0, "dataclass", None),
        ("enum", 0, "StrEnum", "StrEnum"),
    }
    assert definitions == {"SrtSessionActorState", "SrtSessionActorSnapshot"}


def test_korail_reservation_dialog_policy_is_a_stdlib_only_leaf() -> None:
    path = (
        SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "pydoll" / "reservation_dialog_policy.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    definitions = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

    assert imports == {
        ("__future__", 0, "annotations", None),
        ("dataclasses", 0, "dataclass", None),
        ("enum", 0, "StrEnum", None),
    }
    assert definitions == {
        "ReservationDialogControlShape",
        "ReservationDialogAction",
        "ReservationDialogDecision",
        "ReservationDialogEvidence",
        "ReservationDialogKind",
        "ReservationDialogPhase",
    }


@pytest.mark.parametrize(
    ("relative_path", "expected_imports", "expected_imports_from"),
    [
        (
            "rail_waitlist/srt_sidecar/ports.py",
            set(),
            {("__future__", 0), ("typing", 0)},
        ),
        (
            "rail_waitlist/srt_sidecar/application.py",
            set(),
            {
                ("__future__", 0),
                ("collections.abc", 0),
                ("dataclasses", 0),
                ("datetime", 0),
                ("typing", 0),
                ("requests", 0),
                ("SRT", 0),
                ("SRT.errors", 0),
                ("observations.contracts", 2),
                ("provider_account_management.contracts", 2),
                ("reservations.contracts", 2),
                ("reservations.provider_confirmation.contracts", 2),
                ("timetable_management.schemas", 2),
                ("contracts", 1),
                ("session_contract", 1),
            },
        ),
        (
            "rail_waitlist/srt_sidecar/runtime.py",
            {"os"},
            {
                ("__future__", 0),
                ("dataclasses", 0),
                ("typing", 0),
                ("redis.asyncio", 0),
                ("seat_status_cooldown", 2),
                ("provider_adapters.srt_seat_source", 2),
                ("application", 1),
                ("ports", 1),
            },
        ),
        (
            "rail_waitlist/srt_sidecar/reservation.py",
            {"asyncio", "hashlib", "re", "time"},
            {
                ("__future__", 0),
                ("collections.abc", 0),
                ("dataclasses", 0),
                ("datetime", 0),
                ("threading", 0),
                ("typing", 0),
                ("zoneinfo", 0),
                ("pydantic", 0),
                ("requests", 0),
                ("SRT", 0),
                ("SRT.errors", 0),
                ("domain", 2),
                ("provider_adapters.srt_identity", 2),
                ("provider_adapters.srt_netfunnel_logging", 2),
                ("provider_adapters.srt_station_roster", 2),
                ("reservations.contracts", 2),
                ("reservations.provider_confirmation.contracts", 2),
                ("reservations.provider_confirmation.srt", 2),
                ("", 1),
            },
        ),
        (
            "rail_waitlist/srt_sidecar/http.py",
            {"hmac"},
            {
                ("__future__", 0),
                ("collections.abc", 0),
                ("contextlib", 0),
                ("dataclasses", 0),
                ("typing", 0),
                ("fastapi", 0),
                ("fastapi.exception_handlers", 0),
                ("fastapi.exceptions", 0),
                ("fastapi.responses", 0),
                ("starlette.middleware.base", 0),
                ("provider_call_context", 2),
                ("application", 1),
                ("contracts", 1),
                ("ports", 1),
                ("read_only_lifecycle", 1),
            },
        ),
    ],
)
def test_srt_service_owners_have_exact_import_boundaries(
    relative_path: str,
    expected_imports: set[str],
    expected_imports_from: set[tuple[str, int]],
) -> None:
    path = SOURCE_ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module or "", node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert imports == expected_imports
    assert imports_from == expected_imports_from


def test_srt_wire_owner_has_exact_definitions_and_no_runtime_reverse_dependency() -> None:
    path = SOURCE_ROOT / "rail_waitlist" / "srt_sidecar" / "contracts.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for alias in node.names
    }

    assert definitions == {
        "SrtProviderAdapterModel",
        "SrtCredentialRequest",
        "SrtSessionStatus",
        "SrtReadOnlyCallRegistrationRequest",
        "SrtReadOnlyCallRegistrationResult",
        "SrtReadOnlyCallStatus",
        "SrtLoginRequest",
        "SrtLoginResult",
        "SrtObserveRequest",
        "SrtObserveResult",
        "SrtTimetableOverlayRequest",
        "SrtTimetableOverlayResult",
        "SrtTimetableSearchRequest",
        "SrtTimetableTrain",
        "SrtTimetableSearchResult",
        "SrtReserveOnceRequest",
        "SrtReserveOnceResult",
        "SrtReservationConfirmationTarget",
        "SrtConfirmReservationRequest",
        "SrtReservationConfirmationResult",
        "SrtConfirmReservationResult",
    }
    assert imports == set()
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "datetime", None),
        ("domain", 2, "Provider", None),
        ("domain", 2, "SeatClass", None),
        ("observations.contracts", 2, "SeatObservationRequest", None),
        ("observations.contracts", 2, "SeatObservationResult", None),
        ("provider_account_management.contracts", 2, "ProviderCredentials", None),
        ("pydantic", 0, "BaseModel", None),
        ("pydantic", 0, "ConfigDict", None),
        ("pydantic", 0, "Field", None),
        ("pydantic", 0, "SecretStr", None),
        ("pydantic", 0, "model_validator", None),
        ("reservations.contracts", 2, "ReservationRequest", None),
        ("reservations.contracts", 2, "ReservationResult", None),
        (
            "reservations.provider_confirmation.contracts",
            2,
            "ReservationConfirmationOutcome",
            None,
        ),
        (
            "reservations.provider_confirmation.contracts",
            2,
            "ReservationConfirmationPurpose",
            None,
        ),
        (
            "reservations.provider_confirmation.contracts",
            2,
            "ReservationConfirmationResult",
            None,
        ),
        (
            "reservations.provider_confirmation.contracts",
            2,
            "ReservationConfirmationSeat",
            None,
        ),
        (
            "reservations.provider_confirmation.contracts",
            2,
            "ReservationConfirmationTarget",
            None,
        ),
        ("session_contract", 1, "SrtSessionActorState", None),
        ("timetable_management.schemas", 2, "TimetableItem", None),
        ("typing", 0, "Literal", None),
        ("typing", 0, "cast", "_cast"),
        ("zoneinfo", 0, "ZoneInfo", None),
    }


@pytest.mark.parametrize(
    ("relative_path", "owner_module", "owner_alias", "symbols"),
    [
        (
            "rail_waitlist/srt_provider_adapter_contract.py",
            "contracts",
            "_contracts",
            SRT_WIRE_COMPATIBILITY_SYMBOLS,
        ),
        (
            "rail_waitlist/srt_provider_adapter.py",
            "client",
            "_client",
            SRT_CLIENT_COMPATIBILITY_SYMBOLS,
        ),
    ],
)
def test_top_level_srt_sidecar_modules_are_exact_alias_facades(
    relative_path: str,
    owner_module: str,
    owner_alias: str,
    symbols: set[str],
) -> None:
    path = SOURCE_ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
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
        if isinstance(target, ast.Name) and target.id in symbols
    }

    assert definitions == set()
    assert imports == {("srt_sidecar", 1, owner_module, owner_alias)}
    assert set(assignments) == symbols
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == owner_alias
        assert value.attr == symbol


def _module_imports_exact_owner(
    source: str,
    relative_path: Path,
    owner: tuple[str, ...],
) -> bool:
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            tuple(alias.name.split(".")) == owner for alias in node.names
        ):
            return True
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        if resolved == owner[:-1] and any(alias.name == owner[-1] for alias in node.names):
            return True
    return False


@pytest.mark.parametrize(
    ("owner", "symbols", "facade_path"),
    [
        (
            ("rail_waitlist", "provider_accounts"),
            {"ProviderCredentials", "RailLoginMethod"},
            "rail_waitlist/provider_accounts.py",
        ),
        (
            ("rail_waitlist", "srt_reservation"),
            SRT_RESERVATION_COMPATIBILITY_SYMBOLS,
            "rail_waitlist/srt_reservation.py",
        ),
        (
            ("rail_waitlist", "korail_seat_source"),
            (
                KORAIL_SEAT_SOURCE_COMPATIBILITY_SYMBOLS
                | KORAIL_SEAT_SOURCE_PRIVATE_COMPATIBILITY_SYMBOLS
            ),
            "rail_waitlist/korail_seat_source.py",
        ),
        (
            ("rail_waitlist", "srt_seat_source"),
            (SRT_SEAT_SOURCE_COMPATIBILITY_SYMBOLS | SRT_SEAT_SOURCE_PRIVATE_COMPATIBILITY_SYMBOLS),
            "rail_waitlist/srt_seat_source.py",
        ),
        (
            ("rail_waitlist", "srt_provider_adapter_contract"),
            SRT_WIRE_COMPATIBILITY_SYMBOLS,
            "rail_waitlist/srt_provider_adapter_contract.py",
        ),
        (
            ("rail_waitlist", "srt_provider_adapter"),
            SRT_CLIENT_COMPATIBILITY_SYMBOLS,
            "rail_waitlist/srt_provider_adapter.py",
        ),
    ],
)
def test_production_does_not_reenter_provider_legacy_owners(
    owner: tuple[str, ...],
    symbols: set[str],
    facade_path: str,
) -> None:
    violations: list[str] = []

    for path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == facade_path:
            continue
        source = path.read_text(encoding="utf-8")
        if _module_imports_exact_owner(source, relative_path, owner):
            violations.append(f"{relative_path.as_posix()} -> bare-module-import")
        violations.extend(
            _module_contract_references(
                source,
                relative_path,
                owner=owner,
                symbols=symbols,
            )
        )

    assert violations == []


@pytest.mark.parametrize(
    ("owner", "symbols", "excluded_paths", "expected_consumers"),
    [
        (
            ("rail_waitlist", "provider_account_management", "contracts"),
            {"ProviderCredentials"},
            {"rail_waitlist/provider_account_management/contracts.py"},
            {
                "rail_waitlist/korail_browser_seat_source.py",
                "rail_waitlist/provider_adapters/korail_browser_auth_policy.py",
                "rail_waitlist/provider_account_management/application.py",
                "rail_waitlist/provider_account_management/http.py",
                "rail_waitlist/provider_account_management/login_verification.py",
                "rail_waitlist/provider_adapters/execution.py",
                "rail_waitlist/provider_adapters/korail_execution.py",
                "rail_waitlist/provider_adapters/korail_browser_reservation_policy.py",
                "rail_waitlist/provider_account_management/runtime.py",
                "rail_waitlist/srt_sidecar/application.py",
                "rail_waitlist/srt_sidecar/client.py",
                "rail_waitlist/srt_sidecar/contracts.py",
            },
        ),
        (
            ("rail_waitlist", "srt_sidecar", "session_contract"),
            {"SrtSessionActorState", "SrtSessionActorSnapshot"},
            {"rail_waitlist/srt_sidecar/session_contract.py"},
            {
                "rail_waitlist/srt_sidecar/reservation.py",
                "rail_waitlist/srt_sidecar/application.py",
                "rail_waitlist/srt_sidecar/contracts.py",
                "rail_waitlist/provider_account_management/login_verification.py",
            },
        ),
        (
            ("rail_waitlist", "srt_sidecar", "contracts"),
            SRT_WIRE_COMPATIBILITY_SYMBOLS,
            {
                "rail_waitlist/srt_provider_adapter_contract.py",
                "rail_waitlist/srt_sidecar/contracts.py",
            },
            {
                "rail_waitlist/srt_provider_adapter_service.py",
                "rail_waitlist/srt_sidecar/application.py",
                "rail_waitlist/srt_sidecar/client.py",
                "rail_waitlist/srt_sidecar/http.py",
                "rail_waitlist/timetable_management/contracts.py",
                "rail_waitlist/timetable_management/srt_live_timetable.py",
                "rail_waitlist/provider_account_management/login_verification.py",
            },
        ),
        (
            ("rail_waitlist", "srt_sidecar", "reservation"),
            SRT_RESERVATION_COMPATIBILITY_SYMBOLS,
            {
                "rail_waitlist/srt_reservation.py",
                "rail_waitlist/srt_sidecar/reservation.py",
            },
            {
                "rail_waitlist/provider_account_management/login_verification.py",
                "rail_waitlist/provider_adapters/srt_execution.py",
                "rail_waitlist/srt_provider_adapter_service.py",
                "rail_waitlist/worker.py",
            },
        ),
        (
            ("rail_waitlist", "korail_sidecar", "client"),
            KORAIL_BROWSER_CLIENT_SYMBOLS,
            {"rail_waitlist/korail_sidecar/client.py"},
            {
                "rail_waitlist/korail_browser_seat_source.py",
                "rail_waitlist/provider_adapters/korail_browser_query_runtime.py",
                ("rail_waitlist/reservations/provider_confirmation/korail_sidecar_runtime.py"),
            },
        ),
        (
            ("rail_waitlist", "timetable_management", "korail_browser_projection"),
            KORAIL_BROWSER_PROJECTION_SYMBOLS,
            {"rail_waitlist/timetable_management/korail_browser_projection.py"},
            {"rail_waitlist/korail_browser_seat_source.py"},
        ),
        (
            ("rail_waitlist", "provider_adapters", "korail_browser_query_runtime"),
            KORAIL_BROWSER_QUERY_RUNTIME_SYMBOLS,
            {"rail_waitlist/provider_adapters/korail_browser_query_runtime.py"},
            {"rail_waitlist/korail_browser_seat_source.py"},
        ),
        (
            (
                "rail_waitlist",
                "reservations",
                "provider_confirmation",
                "korail_sidecar_runtime",
            ),
            KORAIL_SIDECAR_CONFIRMATION_RUNTIME_SYMBOLS,
            {("rail_waitlist/reservations/provider_confirmation/korail_sidecar_runtime.py")},
            {"rail_waitlist/korail_browser_seat_source.py"},
        ),
        (
            (
                "rail_waitlist",
                "timetable_management",
                "tago_timetable_projection",
            ),
            TAGO_TIMETABLE_PROJECTION_SYMBOLS,
            {"rail_waitlist/timetable_management/tago_timetable_projection.py"},
            {"rail_waitlist/provider_adapters/tago.py"},
        ),
        (
            ("rail_waitlist", "provider_adapters", "korail_seat_source"),
            KORAIL_SEAT_SOURCE_COMPATIBILITY_SYMBOLS,
            {
                "rail_waitlist/korail_seat_source.py",
                "rail_waitlist/provider_adapters/korail_seat_source.py",
            },
            set(),
        ),
        (
            ("rail_waitlist", "provider_adapters", "srt_seat_source"),
            SRT_SEAT_SOURCE_COMPATIBILITY_SYMBOLS,
            {
                "rail_waitlist/srt_seat_source.py",
                "rail_waitlist/provider_adapters/srt_seat_source.py",
            },
            {
                "rail_waitlist/main.py",
                "rail_waitlist/provider_adapters/srt_source_runtime.py",
                "rail_waitlist/srt_sidecar/runtime.py",
                "rail_waitlist/timetable_management/application.py",
            },
        ),
        (
            ("rail_waitlist", "srt_sidecar", "client"),
            SRT_CLIENT_COMPATIBILITY_SYMBOLS,
            {
                "rail_waitlist/srt_provider_adapter.py",
                "rail_waitlist/srt_sidecar/client.py",
            },
            {
                "rail_waitlist/main.py",
                "rail_waitlist/provider_adapters/srt_execution.py",
                "rail_waitlist/provider_adapters/srt_source_runtime.py",
                "rail_waitlist/timetable_management/application.py",
            },
        ),
    ],
)
def test_provider_canonical_owners_have_exact_production_consumers(
    owner: tuple[str, ...],
    symbols: set[str],
    excluded_paths: set[str],
    expected_consumers: set[str],
) -> None:
    consumers: set[str] = set()

    for path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() in excluded_paths:
            continue
        if _module_contract_references(
            path.read_text(encoding="utf-8"),
            relative_path,
            owner=owner,
            symbols=symbols,
        ):
            consumers.add(relative_path.as_posix())

    assert consumers == expected_consumers


def test_srt_sidecar_package_remains_a_passive_namespace() -> None:
    path = SOURCE_ROOT / "rail_waitlist" / "srt_sidecar" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


CENTRAL_MODEL_COMPATIBILITY_SYMBOLS = {
    "AdminAccount",
    "AdminSession",
    "BrowserCompanionChallenge",
    "BrowserCompanionCredential",
    "BrowserCompanionPairing",
    "IdempotencyRecord",
    "KorailBrowserSeatSnapshot",
    "KorailBrowserSnapshotBatch",
    "NativePushCredential",
    "NativePushPairing",
    "NotificationChannel",
    "OfficialPageSeatConfirmation",
    "OutboxEvent",
    "ProviderCircuit",
    "ProviderExecutionLease",
    "RailProviderAccount",
    "ReservationAttempt",
    "SeatObservation",
    "StationCatalogCache",
    "TimetableSeatEvidence",
    "Watch",
    "WatchCandidate",
    "WatchTransitionHistory",
    "utcnow",
}


def _legacy_central_model_references(source: str, relative_path: Path) -> list[str]:
    return _module_contract_references(
        source,
        relative_path,
        owner=("rail_waitlist", "models"),
        symbols=CENTRAL_MODEL_COMPATIBILITY_SYMBOLS,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from rail_waitlist.models import Watch",
        "from rail_waitlist.models import *",
        "import rail_waitlist.models as models; models.Watch",
        "from rail_waitlist import models; models.Watch",
        "import rail_waitlist as rw; rw.models.Watch",
        "import rail_waitlist.models as models; alias = models; alias.Watch",
        "import rail_waitlist.models as models; getattr(models, 'Watch')",
        "import importlib; importlib.import_module('rail_waitlist.models')",
        "from importlib import import_module as load; load('rail_waitlist.models')",
        "__import__('rail_waitlist.models').models.Watch",
    ],
)
def test_legacy_central_model_detector_rejects_all_script_access_forms(source: str) -> None:
    assert _legacy_central_model_references(source, Path("scripts/probe.py"))


def test_operational_scripts_do_not_reenter_the_central_model_hub() -> None:
    script_root = SOURCE_ROOT.parent / "scripts"
    violations: list[str] = []

    for module_path in sorted(script_root.glob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT.parent)
        violations.extend(
            _legacy_central_model_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def _central_schema_compatibility_symbols() -> set[str]:
    module_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module != "__future__":
            symbols.update(
                alias.asname or alias.name
                for alias in node.names
                if not (alias.asname or alias.name).startswith("_")
            )
        elif isinstance(node, ast.Assign):
            symbols.update(
                target.id
                for target in node.targets
                if isinstance(target, ast.Name) and not target.id.startswith("_")
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                symbols.add(node.target.id)
    return symbols


CENTRAL_SCHEMA_COMPATIBILITY_SYMBOLS = _central_schema_compatibility_symbols()


def _legacy_central_schema_references(source: str, relative_path: Path) -> list[str]:
    owner = ("rail_waitlist", "schemas")
    violations = _central_schema_contract_references(
        source,
        relative_path,
        CENTRAL_SCHEMA_COMPATIBILITY_SYMBOLS,
    )
    if _module_imports_exact_owner(source, relative_path, owner):
        violations.append(f"{relative_path.as_posix()} -> central-schema-module")
    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from rail_waitlist.schemas import RailProviderAuthStatus",
        "from rail_waitlist.schemas import *",
        "import rail_waitlist.schemas",
        "import rail_waitlist.schemas as schemas; schemas.ReservationRequest",
        "from rail_waitlist import schemas",
        "import rail_waitlist as rw; rw.schemas.ProviderCapabilities",
        "import rail_waitlist.schemas as schemas; alias = schemas; alias.SeatObservationResult",
        "import rail_waitlist.schemas as schemas; getattr(schemas, 'ReservationResult')",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module as load; load('rail_waitlist.schemas')",
        "__import__('rail_waitlist.schemas').schemas.RailProviderAccountRead",
    ],
)
def test_legacy_central_schema_detector_rejects_all_access_forms(source: str) -> None:
    assert _legacy_central_schema_references(source, Path("rail_waitlist/probe.py"))


def test_production_does_not_reenter_the_central_schema_hub() -> None:
    central_path = "rail_waitlist/schemas.py"
    violations: list[str] = []

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        if relative_path.as_posix() == central_path:
            continue
        violations.extend(
            _legacy_central_schema_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


def test_operational_scripts_do_not_reenter_the_central_schema_hub() -> None:
    script_root = SOURCE_ROOT.parent / "scripts"
    violations: list[str] = []

    for module_path in sorted(script_root.glob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT.parent)
        violations.extend(
            _legacy_central_schema_references(
                module_path.read_text(encoding="utf-8"),
                relative_path,
            )
        )

    assert violations == []


BROWSER_CONTRACT_SYMBOLS = {
    "AdapterErrorReason",
    "AdapterModel",
    "BrowserAdapterError",
    "BrowserClient",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSeatSearchRequest",
    "BrowserSeatSearchResult",
    "BrowserSourceUnavailable",
    "BrowserTrainSnapshot",
    "KorailTrainType",
    "ProtectionTrigger",
    "SOURCE_NAME",
    "SeatStatus",
}
BROWSER_PROTECTION_SYMBOLS = {
    "GENERIC_PROTECTION_TRIGGERS",
    "PROTECTION_MARKERS",
    "RATE_LIMIT_RESOURCE_TYPES",
    "is_rate_limit_response",
    "normalize_replay_protection_trigger",
    "protection_trigger_from_http_response",
    "protection_trigger_from_replay_text",
    "protection_trigger_from_text",
}
KORAIL_BROWSER_AUTOMATION_LEGACY_PUBLIC_SYMBOLS = {
    "ADULT_FARE_PATTERN",
    "AdapterErrorReason",
    "AdapterModel",
    "BaseModel",
    "BrowserAdapterError",
    "BrowserClient",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSeatSearchRequest",
    "BrowserSeatSearchResult",
    "BrowserSourceUnavailable",
    "BrowserTrainSnapshot",
    "Callable",
    "ConfigDict",
    "DELAY_ESTIMATE_PATTERN",
    "DirectCdpLaunchError",
    "FULLSTACK_E2E_PAGE_URL",
    "Field",
    "GENERIC_PROTECTION_TRIGGERS",
    "KST",
    "KorailBrowserAutomation",
    "KorailTrainType",
    "Literal",
    "OFFICIAL_KORAIL_SEARCH_URL",
    "OFFICIAL_TRAIN_TYPE_PATTERN",
    "PROTECTION_MARKERS",
    "PROTECTION_SURFACE_SELECTOR",
    "PlaywrightKorailBrowserClient",
    "ProtectionTrigger",
    "Protocol",
    "RATE_LIMIT_RESOURCE_TYPES",
    "ROUTE_HEADING",
    "SOURCE_NAME",
    "SeatStatus",
    "UTC",
    "ZoneInfo",
    "annotations",
    "asyncio",
    "clock_time",
    "dataclass",
    "date",
    "datetime",
    "field_validator",
    "ipaddress",
    "is_rate_limit_response",
    "is_supported_korail_train_kind",
    "logger",
    "logging",
    "model_validator",
    "open_direct_cdp_browser",
    "parse_expected_delay_minutes",
    "parse_official_train_type",
    "parse_unambiguous_adult_fare",
    "probe_chromium",
    "protection_trigger_from_http_response",
    "protection_trigger_from_text",
    "re",
    "service_datetimes",
    "status_from_seat_box",
    "time",
    "timedelta",
    "urlsplit",
    "validate_korail_general_search_url",
    "visible_departure_matches",
}
KORAIL_BROWSER_AUTOMATION_LEGACY_PRIVATE_SYMBOLS = {
    "_CacheEntry",
    "_Cooldown",
    "_normalize_station",
    "_normalize_train_number",
}
KORAIL_BROWSER_AUTOMATION_LEGACY_SYMBOLS = (
    KORAIL_BROWSER_AUTOMATION_LEGACY_PUBLIC_SYMBOLS
    | KORAIL_BROWSER_AUTOMATION_LEGACY_PRIVATE_SYMBOLS
)


def test_korail_browser_contract_owner_has_exact_definitions_and_imports() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "browser_contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assignments.update(
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )

    assert imports == set()
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("datetime", 0, "date", None),
        ("datetime", 0, "datetime", None),
        ("datetime", 0, "time", "clock_time"),
        ("typing", 0, "Literal", None),
        ("typing", 0, "Protocol", None),
        ("pydantic", 0, "BaseModel", None),
        ("pydantic", 0, "ConfigDict", None),
        ("pydantic", 0, "Field", None),
        ("pydantic", 0, "field_validator", None),
        ("pydantic", 0, "model_validator", None),
        (
            "provider_registry.korail_search_url_policy",
            2,
            "validate_korail_general_search_url",
            None,
        ),
    }
    assert classes == {
        "AdapterModel",
        "BrowserSeatSearchRequest",
        "BrowserTrainSnapshot",
        "BrowserSeatSearchResult",
        "BrowserAdapterError",
        "BrowserProtectionDetected",
        "BrowserRateLimited",
        "BrowserSourceUnavailable",
        "BrowserClient",
    }
    assert functions == set()
    assert assignments == {
        "SeatStatus",
        "KorailTrainType",
        "AdapterErrorReason",
        "ProtectionTrigger",
        "SOURCE_NAME",
    }


def test_korail_browser_protection_owner_has_exact_definitions_and_imports() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "browser_protection.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assignments.update(
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )
    assert imports == {"re"}
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("browser_contracts", 1, "ProtectionTrigger", None),
    }
    assert classes == set()
    assert functions == {
        "is_rate_limit_response",
        "normalize_replay_protection_trigger",
        "protection_trigger_from_http_response",
        "protection_trigger_from_replay_text",
        "protection_trigger_from_text",
    }
    assert assignments == {
        "GENERIC_PROTECTION_TRIGGERS",
        "PROTECTION_MARKERS",
        "RATE_LIMIT_RESOURCE_TYPES",
        "REPLAY_PROTECTION_MARKERS",
        "REPLAY_PROTECTION_TRIGGER_ALIASES",
    }


def test_korail_browser_automation_has_no_moved_contract_or_protection_definitions() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_browser_automation.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assignments.update(
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )

    assert definitions.isdisjoint(BROWSER_CONTRACT_SYMBOLS | BROWSER_PROTECTION_SYMBOLS)
    assert assignments.isdisjoint(BROWSER_CONTRACT_SYMBOLS | BROWSER_PROTECTION_SYMBOLS)


def test_production_uses_canonical_korail_browser_contract_and_protection_owners() -> None:
    contract_owner = ("rail_waitlist", "korail_sidecar", "browser_contracts")
    protection_owner = ("rail_waitlist", "korail_sidecar", "browser_protection")
    contract_consumers: set[str] = set()
    protection_consumers: set[str] = set()

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        relative_name = relative_path.as_posix()
        if relative_name == "rail_waitlist/korail_sidecar/browser_contracts.py":
            continue
        source = module_path.read_text(encoding="utf-8")
        if _module_contract_references(
            source,
            relative_path,
            owner=contract_owner,
            symbols=BROWSER_CONTRACT_SYMBOLS,
        ):
            contract_consumers.add(relative_name)
        if relative_name != "rail_waitlist/korail_sidecar/browser_protection.py" and (
            _module_contract_references(
                source,
                relative_path,
                owner=protection_owner,
                symbols=BROWSER_PROTECTION_SYMBOLS,
            )
        ):
            protection_consumers.add(relative_name)

    assert contract_consumers == {
        "rail_waitlist/korail_sidecar/browser_service_availability.py",
        "rail_waitlist/korail_browser_automation.py",
        "rail_waitlist/korail_browser_mode_smoke.py",
        "rail_waitlist/korail_browser_seat_source.py",
        "rail_waitlist/korail_sidecar/http_replay.py",
        "rail_waitlist/korail_sidecar/pydoll/auth_actor.py",
        "rail_waitlist/korail_pydoll_browser.py",
        "rail_waitlist/korail_pydoll_reservation_actor.py",
        "rail_waitlist/korail_sidecar/pydoll/http_replay.py",
        "rail_waitlist/korail_sidecar/pydoll/dom_interaction.py",
        "rail_waitlist/korail_sidecar/pydoll/login_driver.py",
        "rail_waitlist/korail_sidecar/pydoll/reservation_actor.py",
        "rail_waitlist/korail_sidecar/pydoll/reservation_driver.py",
        "rail_waitlist/korail_sidecar/pydoll/search_actor.py",
        "rail_waitlist/korail_sidecar/pydoll/search_driver.py",
        "rail_waitlist/korail_sidecar/pydoll/search_hour_carousel_input.py",
        "rail_waitlist/korail_sidecar/pydoll/search_hour_carousel_observation.py",
        "rail_waitlist/korail_sidecar/pydoll/search_schedule_commit.py",
        "rail_waitlist/korail_sidecar/browser_protection.py",
        "rail_waitlist/korail_sidecar/client.py",
        "rail_waitlist/korail_sidecar/http.py",
        "rail_waitlist/korail_sidecar/pydoll/chromium_lifecycle.py",
        "rail_waitlist/korail_sidecar/pydoll/page_safety.py",
        "rail_waitlist/korail_sidecar/playwright/client.py",
        "rail_waitlist/korail_sidecar/playwright/result_reader.py",
        "rail_waitlist/korail_sidecar/playwright/search_form.py",
        "rail_waitlist/korail_sidecar/runtime.py",
        "rail_waitlist/korail_sidecar/search_coordinator.py",
        "rail_waitlist/korail_sidecar/search_result_policy.py",
        "rail_waitlist/provider_adapters/korail_browser_query_runtime.py",
        "rail_waitlist/provider_adapters/korail_browser_observation_policy.py",
        "rail_waitlist/timetable_management/korail_browser_projection.py",
    }
    assert protection_consumers == {
        "rail_waitlist/korail_browser_automation.py",
        "rail_waitlist/korail_sidecar/http_replay.py",
        "rail_waitlist/korail_pydoll_browser.py",
        "rail_waitlist/korail_sidecar/pydoll/confirmation_reader.py",
        "rail_waitlist/korail_sidecar/pydoll/http_replay.py",
        "rail_waitlist/korail_sidecar/pydoll/reservation_driver.py",
        "rail_waitlist/korail_sidecar/pydoll/search_driver.py",
        "rail_waitlist/korail_sidecar/pydoll/page_safety.py",
        "rail_waitlist/korail_sidecar/playwright/client.py",
    }


@pytest.mark.parametrize("module_name", ["result_reader.py", "search_form.py"])
def test_playwright_collaborators_do_not_reverse_depend_on_client(module_name: str) -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "playwright" / module_name
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    reverse_imports: set[tuple[str | None, int, str]] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (node.level == 1 and node.module == "client") or (
                    node.level == 1 and node.module is None and alias.name == "client"
                ):
                    reverse_imports.add((node.module, node.level, alias.name))
                if node.level == 0 and node.module == (
                    "rail_waitlist.korail_sidecar.playwright.client"
                ):
                    reverse_imports.add((node.module, node.level, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "rail_waitlist.korail_sidecar.playwright.client":
                    reverse_imports.add((alias.name, 0, alias.name))

    assert reverse_imports == set()


@pytest.mark.parametrize(
    "source",
    [
        "from .korail_browser_automation import KorailBrowserAutomation",
        "from rail_waitlist.korail_browser_automation import _CacheEntry",
        "from .korail_browser_automation import *",
        (
            "import rail_waitlist.korail_browser_automation as legacy; "
            "legacy.PlaywrightKorailBrowserClient"
        ),
        (
            "import rail_waitlist.korail_browser_automation; "
            "rail_waitlist.korail_browser_automation.probe_chromium"
        ),
        (
            "from rail_waitlist import korail_browser_automation as legacy; "
            "legacy.KorailBrowserAutomation"
        ),
        (
            "import rail_waitlist as package; "
            "package.korail_browser_automation.BrowserSeatSearchRequest"
        ),
        (
            "import rail_waitlist.korail_browser_automation as legacy; alias = legacy; "
            "alias._normalize_station"
        ),
        (
            "import rail_waitlist.korail_browser_automation as legacy; "
            "getattr(legacy, 'service_datetimes')"
        ),
        ("import importlib; importlib.import_module('rail_waitlist.korail_browser_automation')"),
        (
            "from importlib import import_module as load; "
            "load('rail_waitlist.korail_browser_automation')"
        ),
        "__import__('rail_waitlist.korail_browser_automation')",
    ],
)
def test_legacy_korail_browser_automation_detector_rejects_all_access_forms(
    source: str,
) -> None:
    assert _module_contract_references(
        source,
        Path("rail_waitlist/probe.py"),
        owner=("rail_waitlist", "korail_browser_automation"),
        symbols=KORAIL_BROWSER_AUTOMATION_LEGACY_SYMBOLS,
    )


def test_production_and_scripts_do_not_reenter_moved_browser_symbols_via_legacy_module() -> None:
    legacy_owner = ("rail_waitlist", "korail_browser_automation")
    assert len(KORAIL_BROWSER_AUTOMATION_LEGACY_PUBLIC_SYMBOLS) == 64
    assert len(KORAIL_BROWSER_AUTOMATION_LEGACY_PRIVATE_SYMBOLS) == 4
    violations: list[str] = []
    roots = [SOURCE_ROOT / "rail_waitlist", SOURCE_ROOT.parent / "scripts"]

    for root in roots:
        for module_path in sorted(root.rglob("*.py")):
            relative_path = module_path.relative_to(
                SOURCE_ROOT.parent if root.name == "scripts" else SOURCE_ROOT
            )
            if relative_path.as_posix() == "rail_waitlist/korail_browser_automation.py":
                continue
            violations.extend(
                _module_contract_references(
                    module_path.read_text(encoding="utf-8"),
                    relative_path,
                    owner=legacy_owner,
                    symbols=KORAIL_BROWSER_AUTOMATION_LEGACY_SYMBOLS,
                )
            )

    assert violations == []


def test_production_and_scripts_do_not_reenter_legacy_korail_http_replay_core() -> None:
    legacy_owner = ("rail_waitlist", "korail_http_replay")
    facade_path = SOURCE_ROOT / "rail_waitlist" / "korail_http_replay.py"
    facade_tree = ast.parse(
        facade_path.read_text(encoding="utf-8"),
        filename=str(facade_path),
    )
    symbols = {
        target.id
        for node in facade_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert len(symbols) == 96

    violations: list[str] = []
    roots = [SOURCE_ROOT / "rail_waitlist", SOURCE_ROOT.parent / "scripts"]
    for root in roots:
        for module_path in sorted(root.rglob("*.py")):
            relative_path = module_path.relative_to(
                SOURCE_ROOT.parent if root.name == "scripts" else SOURCE_ROOT
            )
            if relative_path.as_posix() == "rail_waitlist/korail_http_replay.py":
                continue
            violations.extend(
                _module_contract_references(
                    module_path.read_text(encoding="utf-8"),
                    relative_path,
                    owner=legacy_owner,
                    symbols=symbols,
                )
            )

    assert violations == []


def test_production_and_scripts_do_not_reenter_legacy_pydoll_confirmation_reader() -> None:
    legacy_owner = ("rail_waitlist", "korail_pydoll_confirmation_reader")
    symbols = {
        "Callable",
        "KORAIL_CONFIRMATION_SOURCE",
        "KORAIL_RESERVATION_LIST_SOURCE",
        "KorailConfirmationSession",
        "KorailConfirmationSnapshot",
        "KorailReservationListSession",
        "KorailSameSessionDetailEvidence",
        "PaymentDeadlineParser",
        "Protocol",
        "ReservationConfirmationTarget",
        "UTC",
        "ZoneInfo",
        "_auth_required_evidence",
        "_blocked_evidence",
        "_confirmation_evidence_from_text",
        "_confirmation_snapshot_is_blocked",
        "_has_exact_route_markers",
        "_has_exact_text_marker",
        "_has_exact_train_number_marker",
        "_inconclusive_evidence",
        "_is_complete_detail_evidence",
        "_normalize_station",
        "_parse_korail_payment_deadline",
        "_reservation_date_markers",
        "_session_is_authenticated",
        "annotations",
        "date",
        "datetime",
        "is_rate_limit_response",
        "protection_trigger_from_http_response",
        "protection_trigger_from_text",
        "re",
        "read_korail_same_session_confirmation",
        "runtime_checkable",
        "urlsplit",
    }
    violations: list[str] = []
    roots = [SOURCE_ROOT / "rail_waitlist", SOURCE_ROOT.parent / "scripts"]

    for root in roots:
        for module_path in sorted(root.rglob("*.py")):
            relative_path = module_path.relative_to(
                SOURCE_ROOT.parent if root.name == "scripts" else SOURCE_ROOT
            )
            if relative_path.as_posix() == "rail_waitlist/korail_pydoll_confirmation_reader.py":
                continue
            violations.extend(
                _module_contract_references(
                    module_path.read_text(encoding="utf-8"),
                    relative_path,
                    owner=legacy_owner,
                    symbols=symbols,
                )
            )

    assert violations == []


def test_production_and_scripts_do_not_reenter_legacy_pydoll_http_replay_manager() -> None:
    legacy_owner = ("rail_waitlist", "korail_pydoll_http_replay")
    symbols = {
        "Awaitable",
        "BrowserProtectionDetected",
        "BrowserRateLimited",
        "BrowserSeatSearchRequest",
        "BrowserSeatSearchResult",
        "BrowserSourceUnavailable",
        "Callable",
        "Cleanup",
        "DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE",
        "HttpReplayInvalidCapture",
        "HttpReplayInvalidResponse",
        "HttpReplayLeaseInvalid",
        "HttpReplayProtectionDetected",
        "HttpReplayRateLimited",
        "HttpReplaySessionInvalid",
        "HttpReplaySourceUnavailable",
        "KorailHttpReplayCaptureSession",
        "KorailHttpReplayClientFactory",
        "KorailHttpReplayPlan",
        "KorailHttpReplaySearchClient",
        "Mapping",
        "MappingProxyType",
        "OrderedDict",
        "Protocol",
        "PydollHttpReplayManager",
        "_ActiveHttpReplayLease",
        "_RouteKey",
        "annotations",
        "asyncio",
        "dataclass",
        "date",
        "logger",
        "logging",
        "normalize_replay_protection_trigger",
    }
    violations: list[str] = []
    roots = [SOURCE_ROOT / "rail_waitlist", SOURCE_ROOT.parent / "scripts"]

    for root in roots:
        for module_path in sorted(root.rglob("*.py")):
            relative_path = module_path.relative_to(
                SOURCE_ROOT.parent if root.name == "scripts" else SOURCE_ROOT
            )
            if relative_path.as_posix() == "rail_waitlist/korail_pydoll_http_replay.py":
                continue
            violations.extend(
                _module_contract_references(
                    module_path.read_text(encoding="utf-8"),
                    relative_path,
                    owner=legacy_owner,
                    symbols=symbols,
                )
            )

    assert violations == []


PYDOLL_SEARCH_SNAPSHOT_OWNER_SYMBOLS = {
    "SearchExpansionState",
    "SearchExpansionTransition",
    "advance_search_expansion",
    "begin_search_expansion",
    "deduplicate_search_snapshot",
    "merge_search_snapshots",
    "snapshot_requires_expansion_stop",
    "train_row_identity",
}
PYDOLL_SEARCH_SNAPSHOT_LEGACY_SYMBOLS = {
    "_deduplicate_snapshot",
    "_merge_page_snapshots",
    "_snapshot_requires_expansion_stop",
    "_train_row_identity",
}


def test_pydoll_search_snapshot_policy_has_exact_leaf_boundary() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "pydoll" / "search_snapshot_policy.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assignments.update(
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )

    assert imports == set()
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("collections.abc", 0, "Callable", None),
        ("dataclasses", 0, "dataclass", None),
        ("dataclasses", 0, "replace", None),
        ("page_contracts", 1, "PydollPageSnapshot", None),
        ("page_contracts", 1, "PydollTrainRow", None),
        ("page_safety", 1, "classify_pydoll_page_block", None),
        ("typing", 0, "Literal", None),
    }
    assert definitions == PYDOLL_SEARCH_SNAPSHOT_OWNER_SYMBOLS
    assert assignments == set()


def test_pydoll_browser_keeps_exact_search_snapshot_policy_aliases_without_definitions() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_pydoll_browser.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assignments.update(
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )
    owner_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "korail_sidecar.pydoll.search_snapshot_policy"
        for alias in node.names
    }

    assert definitions.isdisjoint(PYDOLL_SEARCH_SNAPSHOT_LEGACY_SYMBOLS)
    assert assignments.isdisjoint(PYDOLL_SEARCH_SNAPSHOT_LEGACY_SYMBOLS)
    assert owner_imports == {
        (
            "korail_sidecar.pydoll.search_snapshot_policy",
            1,
            "deduplicate_search_snapshot",
            "_deduplicate_snapshot",
        ),
        (
            "korail_sidecar.pydoll.search_snapshot_policy",
            1,
            "merge_search_snapshots",
            "_merge_page_snapshots",
        ),
        (
            "korail_sidecar.pydoll.search_snapshot_policy",
            1,
            "snapshot_requires_expansion_stop",
            "_snapshot_requires_expansion_stop",
        ),
        (
            "korail_sidecar.pydoll.search_snapshot_policy",
            1,
            "train_row_identity",
            "_train_row_identity",
        ),
    }


def test_pydoll_search_snapshot_policy_has_exact_canonical_production_consumers() -> None:
    owner = ("rail_waitlist", "korail_sidecar", "pydoll", "search_snapshot_policy")
    consumers: set[str] = set()

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        relative_name = relative_path.as_posix()
        if relative_name == "rail_waitlist/korail_sidecar/pydoll/search_snapshot_policy.py":
            continue
        if _module_contract_references(
            module_path.read_text(encoding="utf-8"),
            relative_path,
            owner=owner,
            symbols=PYDOLL_SEARCH_SNAPSHOT_OWNER_SYMBOLS,
        ):
            consumers.add(relative_name)

    assert consumers == {
        "rail_waitlist/korail_pydoll_browser.py",
        "rail_waitlist/korail_sidecar/pydoll/search_driver.py",
    }


def test_production_and_scripts_do_not_reenter_legacy_pydoll_snapshot_policy() -> None:
    legacy_owner = ("rail_waitlist", "korail_pydoll_browser")
    violations: list[str] = []
    roots = [SOURCE_ROOT / "rail_waitlist", SOURCE_ROOT.parent / "scripts"]

    for root in roots:
        for module_path in sorted(root.rglob("*.py")):
            relative_path = module_path.relative_to(
                SOURCE_ROOT.parent if root.name == "scripts" else SOURCE_ROOT
            )
            if relative_path.as_posix() == "rail_waitlist/korail_pydoll_browser.py":
                continue
            violations.extend(
                _module_contract_references(
                    module_path.read_text(encoding="utf-8"),
                    relative_path,
                    owner=legacy_owner,
                    symbols=PYDOLL_SEARCH_SNAPSHOT_LEGACY_SYMBOLS,
                )
            )

    assert violations == []


PYDOLL_CHROMIUM_LIFECYCLE_OWNER_SYMBOLS = {
    "PydollChromiumLifecycle",
    "PydollChromiumPhase",
    "PydollChromiumRuntime",
    "cleanup_pydoll_tab_listener",
    "configure_chromium_options",
    "finish_owned_cleanup",
    "probe_pydoll_chromium",
    "set_chromium_binary",
}
PYDOLL_CHROMIUM_LIFECYCLE_LEGACY_SYMBOLS = {
    "_configure_chromium_options",
    "_finish_owned_cleanup",
    "_set_chromium_binary",
    "probe_pydoll_chromium",
}


def test_pydoll_chromium_lifecycle_has_exact_top_level_dependency_boundary() -> None:
    module_path = (
        SOURCE_ROOT / "rail_waitlist" / "korail_sidecar" / "pydoll" / "chromium_lifecycle.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports_from = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == {
        "PydollChromiumPhase",
        "_ChromiumOptions",
        "_PydollTab",
        "_PydollBrowser",
        "_PydollBrowserFactory",
        "_ChromiumOptionsConfigurer",
        "PydollChromiumRuntime",
        "_load_pydoll_runtime",
        "_load_response_received_event",
        "set_chromium_binary",
        "configure_chromium_options",
        "finish_owned_cleanup",
        "cleanup_pydoll_tab_listener",
        "PydollChromiumLifecycle",
        "probe_pydoll_chromium",
    }
    assert imports == {("asyncio", None), ("logging", None), ("os", None)}
    assert imports_from == {
        ("__future__", 0, "annotations", None),
        ("collections.abc", 0, "Awaitable", None),
        ("collections.abc", 0, "Callable", None),
        ("dataclasses", 0, "dataclass", None),
        ("enum", 0, "StrEnum", None),
        ("pathlib", 0, "Path", None),
        ("typing", 0, "Any", None),
        ("typing", 0, "Protocol", None),
        ("typing", 0, "cast", None),
        ("browser_contracts", 2, "BrowserSourceUnavailable", None),
        ("chromium_launch", 2, "isolated_test_chromium_arguments", None),
    }


def test_pydoll_browser_keeps_lifecycle_compatibility_aliases_without_redefinition() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "korail_pydoll_browser.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    owner_imports = {
        (alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "korail_sidecar.pydoll.chromium_lifecycle"
        for alias in node.names
    }

    assert definitions.isdisjoint(PYDOLL_CHROMIUM_LIFECYCLE_LEGACY_SYMBOLS)
    assert owner_imports == {
        ("PydollChromiumLifecycle", None),
        ("cleanup_pydoll_tab_listener", None),
        ("configure_chromium_options", "_configure_chromium_options"),
        ("finish_owned_cleanup", "_finish_owned_cleanup"),
        ("probe_pydoll_chromium", "probe_pydoll_chromium"),
        ("set_chromium_binary", "_set_chromium_binary"),
    }


def test_pydoll_chromium_lifecycle_has_exact_canonical_production_consumers() -> None:
    owner = ("rail_waitlist", "korail_sidecar", "pydoll", "chromium_lifecycle")
    consumers: set[str] = set()

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        relative_name = relative_path.as_posix()
        if relative_name == "rail_waitlist/korail_sidecar/pydoll/chromium_lifecycle.py":
            continue
        if _module_contract_references(
            module_path.read_text(encoding="utf-8"),
            relative_path,
            owner=owner,
            symbols=PYDOLL_CHROMIUM_LIFECYCLE_OWNER_SYMBOLS,
        ):
            consumers.add(relative_name)

    assert consumers == {
        "rail_waitlist/korail_pydoll_browser.py",
        "rail_waitlist/korail_sidecar/runtime.py",
    }


def test_production_and_scripts_do_not_reenter_legacy_pydoll_lifecycle_symbols() -> None:
    legacy_owner = ("rail_waitlist", "korail_pydoll_browser")
    violations: list[str] = []
    roots = [SOURCE_ROOT / "rail_waitlist", SOURCE_ROOT.parent / "scripts"]

    for root in roots:
        for module_path in sorted(root.rglob("*.py")):
            relative_path = module_path.relative_to(
                SOURCE_ROOT.parent if root.name == "scripts" else SOURCE_ROOT
            )
            if relative_path.as_posix() == "rail_waitlist/korail_pydoll_browser.py":
                continue
            violations.extend(
                _module_contract_references(
                    module_path.read_text(encoding="utf-8"),
                    relative_path,
                    owner=legacy_owner,
                    symbols=PYDOLL_CHROMIUM_LIFECYCLE_LEGACY_SYMBOLS,
                )
            )

    assert violations == []
