from __future__ import annotations

import ast
from pathlib import Path

from rail_waitlist import schemas as legacy

API_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = API_ROOT / "src" / "rail_waitlist" / "schemas.py"

SCHEMA_ALIASES = {
    "AuthStatus",
    "BrowserCompanionChallengeCreate",
    "BrowserCompanionChallengeRead",
    "BrowserCompanionCredentialRead",
    "BrowserCompanionPairingCreate",
    "BrowserCompanionPairingExchange",
    "BrowserCompanionPairingRead",
    "BrowserCompanionPairingResult",
    "BrowserCompanionStatus",
    "ErrorPolicyResult",
    "EventRead",
    "HealthResponse",
    "KORAIL_BROWSER_COMPANION_SOURCE",
    "KorailBrowserSeatStatus",
    "KorailBrowserSnapshotCreate",
    "KorailBrowserSnapshotRead",
    "KorailBrowserSnapshotRevision",
    "KorailBrowserTrainSnapshot",
    "LoginResult",
    "NotificationChannelCreate",
    "NotificationChannelRead",
    "NotificationChannelUpdate",
    "OFFICIAL_PAGE_CONFIRMATION_SOURCE",
    "OfficialPageSeatConfirmationCreate",
    "OfficialPageSeatConfirmationItem",
    "OfficialPageSeatConfirmationItemRead",
    "OfficialPageSeatConfirmationRead",
    "OfficialPageSeatStatus",
    "OperationCurrentCounts",
    "OperationEntry",
    "OperationEntryKind",
    "OperationEntryLevel",
    "OperationEntryReasonCode",
    "OperationFreshness",
    "OperationProviderCircuit",
    "OperationRate",
    "OperationServiceState",
    "OperationServiceStatus",
    "OperationSourceFreshness",
    "OperationStatusCount",
    "OperationWindowCounts",
    "OperationsSummary",
    "OperationsWindow",
    "ProviderCapabilities",
    "QueuedResponse",
    "RailLoginMethod",
    "RailProviderAccountRead",
    "RailProviderAccountUpsert",
    "RailProviderAuthStatus",
    "RailProviderRuntimeState",
    "RailProviderRuntimeStatusRead",
    "RegistrationEvidenceConflictDetail",
    "SeatAvailability",
    "SeatAvailabilityAction",
    "SeatAvailabilityNotObservedReason",
    "SeatAvailabilityProvenance",
    "SeatAvailabilityStatus",
    "SeatClassAvailability",
    "SeatStatusCooldownCause",
    "SeatStatusRefreshRequest",
    "SeatStatusSourceStatus",
    "StationCatalog",
    "StationItem",
    "TimetableItem",
    "TimetableSeatEvidenceRead",
    "UiPreferencesRead",
    "UiPreferencesUpdate",
    "UsernamePasswordCredentials",
    "WatchCandidateCreate",
    "WatchCandidateLatestObservationRead",
    "WatchCandidateLatestReservationAttemptRead",
    "WatchCandidateRead",
    "WatchCreate",
    "WatchRead",
    "WatchUpdate",
    "contains_protection_marker",
    "normalize_official_train_number",
}

SCHEMA_IMPORTS = {
    ("__future__", 0, "annotations", None),
    (None, 1, "official_rail_identity", None),
    ("admin_auth", 1, "schemas", "admin_auth_schemas"),
    ("browser_companion", 1, "schemas", "browser_companion_schemas"),
    ("event_stream", 1, "schemas", "event_stream_schemas"),
    ("health", 1, "schemas", "health_schemas"),
    ("notification_management", 1, "schemas", "notification_management_schemas"),
    ("observations.contracts", 1, "ObservationErrorCategory", "ObservationErrorCategory"),
    ("observations.contracts", 1, "SeatObservationRequest", "SeatObservationRequest"),
    ("observations.contracts", 1, "SeatObservationResult", "SeatObservationResult"),
    ("official_page_confirmation", 1, "schemas", "official_page_confirmation_schemas"),
    ("operation_summary", 1, "schemas", "operation_summary_schemas"),
    ("provider_account_management", 1, "schemas", "provider_account_management_schemas"),
    ("provider_registry", 1, "contracts", "provider_registry_contracts"),
    (
        "provider_registry.korail_search_url_policy",
        1,
        "validate_korail_general_search_url",
        "validate_korail_general_search_url",
    ),
    ("provider_registry.official_url_policy", 1, "OFFICIAL_HOST_ROOTS", "OFFICIAL_HOST_ROOTS"),
    (
        "provider_registry.official_url_policy",
        1,
        "is_official_provider_host",
        "is_official_provider_host",
    ),
    ("provider_schema_base", 1, "ProviderContractModel", "ProviderContractModel"),
    ("reservations.contracts", 1, "ReservationProgressStage", "ReservationProgressStage"),
    (
        "reservations.contracts",
        1,
        "ReservationProgressStageName",
        "ReservationProgressStageName",
    ),
    ("reservations.contracts", 1, "ReservationRequest", "ReservationRequest"),
    ("reservations.contracts", 1, "ReservationResult", "ReservationResult"),
    ("seat_status_operations", 1, "schemas", "seat_status_operations_schemas"),
    ("timetable_management", 1, "schemas", "timetable_management_schemas"),
    ("ui_preferences", 1, "schemas", "ui_preferences_schemas"),
    ("watch_management", 1, "provider_failure_policy", "watch_provider_failure_policy"),
    ("watch_management", 1, "schemas", "watch_management_schemas"),
}


def test_central_schemas_is_an_exact_definition_free_alias_hub() -> None:
    tree = ast.parse(SCHEMA_PATH.read_text(encoding="utf-8"), filename=str(SCHEMA_PATH))
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
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert definitions == set()
    assert assignments == SCHEMA_ALIASES
    assert imports == SCHEMA_IMPORTS
    assert not hasattr(legacy, "__all__")
    assert len({name for name in vars(legacy) if not name.startswith("_")}) == 104
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == set()


def test_central_schema_aliases_are_owned_outside_the_compatibility_hub() -> None:
    for name in SCHEMA_ALIASES:
        value = getattr(legacy, name)
        defining_module = getattr(value, "__module__", None)
        if defining_module is not None:
            assert defining_module != "rail_waitlist.schemas"
