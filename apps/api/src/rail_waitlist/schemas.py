from __future__ import annotations

from datetime import UTC, date, datetime, time, timezone
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import (
    AnyHttpUrl,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .domain import (
    BookingWindowStatus,
    OperationalStatus,
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationMode,
    SeatObservationStatus,
    WatchStatus,
)
from .korail_search_bootstrap import validate_korail_general_search_url
from .notification_management import schemas as notification_management_schemas
from .operation_summary import schemas as operation_summary_schemas
from .provider_account_management import schemas as provider_account_management_schemas
from .schema_base import ApiModel
from .ui_preferences import schemas as ui_preferences_schemas

OperationCurrentCounts = operation_summary_schemas.OperationCurrentCounts
OperationEntry = operation_summary_schemas.OperationEntry
OperationEntryKind = operation_summary_schemas.OperationEntryKind
OperationEntryLevel = operation_summary_schemas.OperationEntryLevel
OperationFreshness = operation_summary_schemas.OperationFreshness
OperationProviderCircuit = operation_summary_schemas.OperationProviderCircuit
OperationRate = operation_summary_schemas.OperationRate
OperationServiceState = operation_summary_schemas.OperationServiceState
OperationServiceStatus = operation_summary_schemas.OperationServiceStatus
OperationSourceFreshness = operation_summary_schemas.OperationSourceFreshness
OperationsSummary = operation_summary_schemas.OperationsSummary
OperationStatusCount = operation_summary_schemas.OperationStatusCount
OperationsWindow = operation_summary_schemas.OperationsWindow
OperationWindowCounts = operation_summary_schemas.OperationWindowCounts
NotificationChannelCreate = notification_management_schemas.NotificationChannelCreate
NotificationChannelRead = notification_management_schemas.NotificationChannelRead
NotificationChannelUpdate = notification_management_schemas.NotificationChannelUpdate
QueuedResponse = notification_management_schemas.QueuedResponse
RailLoginMethod = provider_account_management_schemas.RailLoginMethod
RailProviderAccountRead = provider_account_management_schemas.RailProviderAccountRead
RailProviderAccountUpsert = provider_account_management_schemas.RailProviderAccountUpsert
RailProviderAuthStatus = provider_account_management_schemas.RailProviderAuthStatus
RailProviderRuntimeState = provider_account_management_schemas.RailProviderRuntimeState
RailProviderRuntimeStatusRead = provider_account_management_schemas.RailProviderRuntimeStatusRead
UiPreferencesRead = ui_preferences_schemas.UiPreferencesRead
UiPreferencesUpdate = ui_preferences_schemas.UiPreferencesUpdate


class RegistrationEvidenceConflictDetail(ApiModel):
    code: Literal["registration_evidence_conflict"] = "registration_evidence_conflict"
    reason: Literal["expired"]
    message: str = Field(min_length=1, max_length=240)


class ProviderCapabilities(ApiModel):
    provider: Provider
    timetable: bool
    official_booking_link: bool
    official_waitlist_link: bool
    seat_monitoring: bool
    reservation_once: bool
    experimental: bool = False
    enabled: bool = True
    note: str | None = None


class StationItem(ApiModel):
    node_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    city_code: str = Field(min_length=1, max_length=20)
    city_name: str = Field(min_length=1, max_length=80)


class StationCatalog(ApiModel):
    provider: Provider
    source: Literal["TAGO", "mock"]
    retrieved_at: datetime
    catalog_scope: Literal[
        "all_tago_train_stations",
        "intercity_station_guide_intersection",
        "mock",
    ]
    provider_membership: Literal["not_verified_by_source", "mock"]
    note: str = Field(min_length=1, max_length=240)
    stations: list[StationItem]

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieved_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone")
        return value


SeatAvailabilityStatus = Literal[
    "unavailable",
    "unknown",
    "available",
    "limited",
    "standing_plus_seat",
    "not_enough_seats",
    "sold_out",
    "waitlist_available",
    "reservation_completed",
    "not_offered",
    "departed",
    "out_of_service",
    "stale",
    "error",
]

ObservationErrorCategory = Literal[
    "timeout",
    "schema_mismatch",
    "provider_unavailable",
    "partial_failure",
    "unknown",
]
OFFICIAL_HOST_ROOTS = {
    Provider.KORAIL: ("korail.com", "letskorail.com"),
    Provider.SRT: ("srail.kr",),
    Provider.MOCK: ("example.invalid",),
}


def is_official_provider_host(provider: Provider, value: AnyHttpUrl) -> bool:
    host = (value.host or "").lower().rstrip(".")
    return any(host == root or host.endswith(f".{root}") for root in OFFICIAL_HOST_ROOTS[provider])


class ProviderContractModel(ApiModel):
    """Strict provider boundary that cannot carry arbitrary transport or secret fields."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")


OFFICIAL_PAGE_CONFIRMATION_SOURCE = "official-page-user-confirmation"
KORAIL_BROWSER_COMPANION_SOURCE = "korail-official-browser-companion"
OfficialPageSeatStatus = Literal[
    "available",
    "sold_out",
    "waitlist_available",
    "not_offered",
]

KorailBrowserSeatStatus = Literal[
    "available",
    "limited",
    "standing_plus_seat",
    "sold_out",
    "waitlist_available",
    "not_offered",
]


def normalize_official_train_number(value: str) -> str:
    normalized = value.strip().upper()
    if normalized.isdecimal():
        return normalized.lstrip("0") or "0"
    return normalized


def contains_protection_marker(value: str) -> bool:
    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in ("-8002", "-8003", "macro_err", "captcha", "netfunnel", "blocked")
    )


class KorailBrowserTrainSnapshot(ProviderContractModel):
    train_number: str = Field(min_length=1, max_length=40)
    departure_at: datetime
    standard: KorailBrowserSeatStatus
    first: KorailBrowserSeatStatus

    @field_validator("train_number")
    @classmethod
    def normalize_train_number(cls, value: str) -> str:
        normalized = normalize_official_train_number(value)
        if not normalized:
            raise ValueError("train_number cannot be blank")
        if contains_protection_marker(normalized):
            raise ValueError("protection markers are not train identity")
        return normalized

    @field_validator("departure_at")
    @classmethod
    def require_aware_departure(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("departure_at must include a timezone")
        return value.astimezone(UTC).replace(microsecond=0)


class KorailBrowserSnapshotCreate(ProviderContractModel):
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    travel_date: date
    passenger_count: int = Field(ge=1, le=9)
    trains: list[KorailBrowserTrainSnapshot] = Field(min_length=1, max_length=100)

    @field_validator("origin", "destination")
    @classmethod
    def normalize_route_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("route names cannot be blank")
        if contains_protection_marker(normalized):
            raise ValueError("protection markers are not route identity")
        return normalized

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> "KorailBrowserSnapshotCreate":
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        train_numbers = [train.train_number for train in self.trains]
        if len(train_numbers) != len(set(train_numbers)):
            raise ValueError("trains must contain unique train numbers")
        korea = ZoneInfo("Asia/Seoul")
        if any(
            train.departure_at.astimezone(korea).date() != self.travel_date for train in self.trains
        ):
            raise ValueError("train departure date must match travel_date in Asia/Seoul")
        return self


class KorailBrowserSnapshotRead(ApiModel):
    batch_id: str
    accepted_trains: int
    accepted_seats: int
    source: Literal["korail-official-browser-companion"]
    observed_at: datetime
    fresh_until: datetime


class KorailBrowserSnapshotRevision(ApiModel):
    revision: datetime | None


class BrowserCompanionPairingCreate(ApiModel):
    label: str = Field(default="내 브라우저", min_length=1, max_length=80)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("pairing label cannot be blank")
        return normalized


class BrowserCompanionPairingRead(ApiModel):
    pairing_code: str
    expires_at: datetime


class BrowserCompanionPairingExchange(ApiModel):
    pairing_code: str = Field(min_length=32, max_length=256)
    client_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )


class BrowserCompanionPairingResult(ApiModel):
    credential_id: str
    bridge_token: str
    label: str


class BrowserCompanionCredentialRead(ApiModel):
    id: str
    label: str
    extension_origin: str
    created_at: datetime
    last_used_at: datetime | None


class BrowserCompanionStatus(ApiModel):
    enabled: bool
    credentials: list[BrowserCompanionCredentialRead]


class BrowserCompanionChallengeCreate(ApiModel):
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BrowserCompanionChallengeRead(ApiModel):
    challenge: str
    expires_at: datetime


class OfficialPageSeatConfirmationItem(ProviderContractModel):
    seat_class: SeatClass
    status: OfficialPageSeatStatus

    @model_validator(mode="after")
    def require_supported_class(self) -> OfficialPageSeatConfirmationItem:
        if self.seat_class not in {SeatClass.STANDARD, SeatClass.FIRST}:
            raise ValueError("official page confirmations require standard or first class")
        return self


class OfficialPageSeatConfirmationCreate(ProviderContractModel):
    """A normalized atomic batch; source and observation time are server-owned."""

    provider: Provider
    origin_node_id: str = Field(min_length=1, max_length=80)
    destination_node_id: str = Field(min_length=1, max_length=80)
    train_number: str = Field(min_length=1, max_length=40)
    departure_at: datetime
    passenger_count: int = Field(ge=1, le=9)
    seat_classes: list[OfficialPageSeatConfirmationItem] = Field(min_length=1, max_length=2)

    @field_validator("origin_node_id", "destination_node_id")
    @classmethod
    def normalize_station_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("station node IDs cannot be blank")
        if contains_protection_marker(normalized):
            raise ValueError("protection markers are not seat confirmation identity")
        return normalized

    @field_validator("train_number")
    @classmethod
    def normalize_train_number(cls, value: str) -> str:
        normalized = normalize_official_train_number(value)
        if not normalized:
            raise ValueError("train_number cannot be blank")
        if contains_protection_marker(normalized):
            raise ValueError("protection markers are not train numbers")
        return normalized

    @field_validator("departure_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("departure_at must include a timezone")
        return value.astimezone(UTC).replace(microsecond=0)

    @model_validator(mode="after")
    def validate_confirmation(self) -> OfficialPageSeatConfirmationCreate:
        if self.provider not in {Provider.KORAIL, Provider.SRT}:
            raise ValueError("official page confirmations only accept KORAIL or SRT")
        if self.origin_node_id == self.destination_node_id:
            raise ValueError("origin and destination node IDs must differ")
        classes = [item.seat_class for item in self.seat_classes]
        if len(classes) != len(set(classes)):
            raise ValueError("seat_classes must contain unique seat classes")
        return self


class OfficialPageSeatConfirmationItemRead(ApiModel):
    id: str
    seat_class: SeatClass
    status: OfficialPageSeatStatus


class OfficialPageSeatConfirmationRead(ApiModel):
    provider: Provider
    origin_node_id: str
    destination_node_id: str
    train_number: str
    departure_at: datetime
    passenger_count: int
    seat_classes: list[OfficialPageSeatConfirmationItemRead]
    source: Literal["official-page-user-confirmation"]
    provenance_kind: Literal["user_confirmed_official_page"] = "user_confirmed_official_page"
    observed_at: datetime
    fresh_until: datetime
    created_count: int
    replayed: bool

    @field_validator("departure_at", "observed_at", "fresh_until")
    @classmethod
    def normalize_response_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class SeatObservationRequest(ProviderContractModel):
    provider: Provider
    origin_node_id: str = Field(min_length=1, max_length=80)
    destination_node_id: str = Field(min_length=1, max_length=80)
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    train_number: str = Field(min_length=1, max_length=40)
    departure_at: datetime
    seat_class: SeatClass
    passenger_count: int = Field(ge=1, le=9)

    @field_validator("origin_node_id", "destination_node_id", "train_number")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider request identifiers cannot be blank")
        return normalized

    @field_validator("origin", "destination")
    @classmethod
    def normalize_station_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider request station names cannot be blank")
        return normalized

    @field_validator("departure_at")
    @classmethod
    def require_aware_departure_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("departure_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_observation_target(self) -> SeatObservationRequest:
        if self.origin_node_id == self.destination_node_id:
            raise ValueError("origin and destination node IDs must differ")
        if self.origin == self.destination:
            raise ValueError("origin and destination station names must differ")
        if self.seat_class == SeatClass.ANY:
            raise ValueError("provider requests require a concrete seat class")
        return self


class SeatObservationResult(ProviderContractModel):
    seat_class: SeatClass
    status: SeatObservationStatus
    source: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    observed_at: datetime
    fresh_until: datetime
    error_category: ObservationErrorCategory | None = None
    delay_minutes: int | None = Field(default=None, ge=1, le=999)

    @field_validator("observed_at", "fresh_until")
    @classmethod
    def require_aware_observation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observation times must include a timezone")
        return value

    @field_validator("source", mode="before")
    @classmethod
    def normalize_observation_source(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_observation_result(self) -> SeatObservationResult:
        if self.seat_class == SeatClass.ANY:
            raise ValueError("seat observations require a concrete seat class")
        if self.fresh_until < self.observed_at:
            raise ValueError("fresh_until cannot be earlier than observed_at")
        if self.status == "error" and self.error_category is None:
            raise ValueError("error observations require an error_category")
        if self.status not in {"error", "unknown"} and self.error_category is not None:
            raise ValueError("successful observations cannot include an error_category")
        return self


class ReservationRequest(SeatObservationRequest):
    candidate_id: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=160)
    expected_credential_version: int | None = Field(default=None, ge=1)
    arrival_at: datetime | None = None

    @field_validator("candidate_id", "idempotency_key")
    @classmethod
    def normalize_reservation_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reservation identifiers cannot be blank")
        return normalized

    @field_validator("arrival_at")
    @classmethod
    def require_aware_arrival_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("arrival_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_reservation_times(self) -> "ReservationRequest":
        if self.arrival_at is not None and self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be later than departure_at")
        return self


ReservationProgressStageName = Literal[
    "authenticated_session_ready",
    "target_rechecked",
    "seat_selected",
    "reservation_requested",
]


class ReservationProgressStage(ProviderContractModel):
    stage: ReservationProgressStageName
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def require_aware_progress_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reservation progress times must include a timezone")
        return value


class ReservationResult(ProviderContractModel):
    """A reserved outcome is a temporary hold, never a completed payment."""

    outcome: ReservationOutcome = Field(
        description=(
            "reserved means a temporary reservation awaiting user payment, not payment completion"
        )
    )
    source: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    observed_at: datetime
    credential_version: int | None = Field(default=None, ge=1)
    payment_deadline: datetime | None = None
    official_handoff_url: AnyHttpUrl | None = None
    progress_stages: tuple[ReservationProgressStage, ...] = ()

    @field_validator("observed_at", "payment_deadline")
    @classmethod
    def require_aware_reservation_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("reservation times must include a timezone")
        return value

    @field_validator("source", mode="before")
    @classmethod
    def normalize_reservation_source(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("official_handoff_url")
    @classmethod
    def require_safe_handoff_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is None:
            return None
        if value.scheme != "https" or value.username is not None or value.password is not None:
            raise ValueError("official_handoff_url must be a credential-free HTTPS URL")
        host = (value.host or "").lower().rstrip(".")
        allowed_roots = tuple(root for roots in OFFICIAL_HOST_ROOTS.values() for root in roots)
        if not any(host == root or host.endswith(f".{root}") for root in allowed_roots):
            raise ValueError("official_handoff_url must use an approved provider host")
        return value

    @model_validator(mode="after")
    def validate_reservation_result(self) -> ReservationResult:
        stage_names = [progress.stage for progress in self.progress_stages]
        if len(stage_names) != len(set(stage_names)):
            raise ValueError("reservation progress stages must be unique")
        progress_times = [progress.occurred_at for progress in self.progress_stages]
        if progress_times != sorted(progress_times):
            raise ValueError("reservation progress stages must be chronological")
        if any(progress_time > self.observed_at for progress_time in progress_times):
            raise ValueError("reservation progress cannot occur after the result observation")
        actionable = self.outcome in {"payment_required", "reserved"}
        if actionable and self.official_handoff_url is None:
            raise ValueError(
                f"{self.outcome} is not payment completion and requires an official_handoff_url"
            )
        if not actionable and (
            self.payment_deadline is not None or self.official_handoff_url is not None
        ):
            raise ValueError("only payment_required or reserved can include payment handoff data")
        if self.payment_deadline is not None and self.payment_deadline <= self.observed_at:
            raise ValueError("payment_deadline must be later than observed_at")
        if (
            self.source == "mock"
            and self.official_handoff_url is not None
            and not is_official_provider_host(Provider.MOCK, self.official_handoff_url)
        ):
            raise ValueError("mock reservation handoff must use the mock provider host")
        if (
            self.source != "mock"
            and self.official_handoff_url is not None
            and is_official_provider_host(Provider.MOCK, self.official_handoff_url)
        ):
            raise ValueError("official provider handoff cannot use the mock provider host")
        return self


class SeatAvailability(ApiModel):
    status: SeatAvailabilityStatus = "unavailable"
    source: str | None = None
    observed_at: datetime | None = None


SeatAvailabilityNotObservedReason = Literal[
    "public_api_not_available",
    "source_not_configured",
    "provider_access_restricted",
    "unsupported_route",
    "passenger_count_not_supported",
    "departure_window_elapsed",
    "no_exact_match",
    "source_unavailable",
]


class SeatAvailabilityProvenance(ApiModel):
    kind: Literal[
        "not_observed",
        "official_provider",
        "official_page_browser_companion",
        "user_confirmed_official_page",
        "mock",
    ]
    source: str | None = Field(default=None, min_length=1, max_length=80)
    observed_at: datetime | None = None
    fresh_until: datetime | None = None
    reason: SeatAvailabilityNotObservedReason | None = None

    @field_validator("source")
    @classmethod
    def reject_blank_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("source cannot be blank")
        return normalized

    @field_validator("observed_at", "fresh_until")
    @classmethod
    def require_aware_evidence_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("provenance timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_evidence(self) -> "SeatAvailabilityProvenance":
        if self.kind == "not_observed":
            if (
                self.source is not None
                or self.observed_at is not None
                or self.fresh_until is not None
            ):
                raise ValueError("not_observed provenance cannot contain observation evidence")
            if self.reason is None:
                raise ValueError("not_observed provenance requires a reason")
            return self
        if self.source is None or self.observed_at is None:
            raise ValueError("observed provenance requires source and observed_at")
        if self.kind in {
            "user_confirmed_official_page",
            "official_page_browser_companion",
        }:
            expected_source = (
                OFFICIAL_PAGE_CONFIRMATION_SOURCE
                if self.kind == "user_confirmed_official_page"
                else KORAIL_BROWSER_COMPANION_SOURCE
            )
            if self.source != expected_source:
                raise ValueError("official-page provenance requires its fixed source")
            if self.fresh_until is None:
                raise ValueError("official-page provenance requires fresh_until")
            if self.fresh_until <= self.observed_at:
                raise ValueError("official-page fresh_until must be later than observed_at")
        elif self.fresh_until is not None and self.fresh_until < self.observed_at:
            raise ValueError("fresh_until cannot be earlier than observed_at")
        if self.reason is not None:
            raise ValueError("observed provenance cannot use a not-observed reason")
        return self


class SeatAvailabilityAction(ApiModel):
    kind: Literal[
        "official_check",
        "add_to_watch",
        "official_waitlist",
        "retry_provider",
    ]
    url: AnyHttpUrl | None = None

    @model_validator(mode="after")
    def validate_url(self) -> "SeatAvailabilityAction":
        external_actions = {"official_check", "official_waitlist"}
        if self.kind in external_actions:
            if self.url is None or self.url.scheme != "https":
                raise ValueError(f"{self.kind} requires an HTTPS URL")
        elif self.url is not None:
            raise ValueError(f"{self.kind} cannot include a URL")
        return self


class SeatClassAvailability(ApiModel):
    seat_class: SeatClass
    status: SeatAvailabilityStatus
    provenance: SeatAvailabilityProvenance
    fare: int | None = Field(default=None, ge=0)
    fare_currency: Literal["KRW"] = "KRW"
    actions: list[SeatAvailabilityAction] = Field(default_factory=list, max_length=4)
    registration_evidence_id: str | None = Field(default=None, min_length=1, max_length=36)

    @model_validator(mode="after")
    def validate_status_evidence(self) -> "SeatClassAvailability":
        if self.seat_class == SeatClass.ANY:
            raise ValueError("per-class availability cannot use the any seat class")
        observed_statuses = {
            "available",
            "limited",
            "standing_plus_seat",
            "not_enough_seats",
            "sold_out",
            "waitlist_available",
            "reservation_completed",
            "not_offered",
            "departed",
            "out_of_service",
        }
        if self.status in observed_statuses and self.provenance.kind == "not_observed":
            raise ValueError(f"{self.status} requires observed provider provenance")
        if self.provenance.kind == "not_observed" and self.status != "unknown":
            raise ValueError("not_observed provenance must report unknown status")
        if self.fare is not None and self.provenance.kind == "not_observed":
            raise ValueError("per-class fare requires observed provider provenance")
        return self


class TimetableSeatEvidenceRead(ApiModel):
    id: str
    status: SeatAvailabilityStatus
    provenance: SeatAvailabilityProvenance
    created_at: datetime
    registration_valid_until: datetime

    @field_validator("created_at", "registration_valid_until", mode="before")
    @classmethod
    def normalize_evidence_timezone(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class TimetableItem(ApiModel):
    provider: Provider
    train_number: str
    train_type: str
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    adult_fare: int | None = Field(default=None, ge=0)
    fare_currency: Literal["KRW"] = "KRW"
    timetable_source: Literal["official_provider", "TAGO", "mock"]
    timetable_retrieved_at: datetime
    availability: SeatAvailability = Field(default_factory=SeatAvailability)
    seat_classes: list[SeatClassAvailability] = Field(default_factory=list, max_length=5)
    official_booking_url: AnyHttpUrl
    official_search_url: AnyHttpUrl | None = None

    @field_validator("official_booking_url", "official_search_url")
    @classmethod
    def require_https_booking_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is None:
            return None
        if value.scheme != "https":
            raise ValueError("official provider URLs must use HTTPS")
        return value

    @field_validator("seat_classes")
    @classmethod
    def reject_duplicate_seat_classes(
        cls, value: list[SeatClassAvailability]
    ) -> list[SeatClassAvailability]:
        classes = [item.seat_class for item in value]
        if len(classes) != len(set(classes)):
            raise ValueError("seat_classes must contain unique seat classes")
        return value

    @model_validator(mode="after")
    def require_provider_official_hosts(self) -> "TimetableItem":
        if not is_official_provider_host(self.provider, self.official_booking_url):
            raise ValueError("official_booking_url must use the provider's official host")
        if self.provider is not Provider.KORAIL and self.official_search_url is not None:
            raise ValueError("only KORAIL timetable items may contain a KORAIL search URL")
        if self.provider is Provider.KORAIL and self.official_search_url is not None:
            validate_korail_general_search_url(str(self.official_search_url))
        for seat in self.seat_classes:
            for action in seat.actions:
                if action.kind in {"official_check", "official_waitlist"} and (
                    action.url is None or not is_official_provider_host(self.provider, action.url)
                ):
                    raise ValueError("official seat action must use the provider's official host")
        return self


class SeatStatusRefreshRequest(ApiModel):
    provider: Literal[Provider.KORAIL, Provider.SRT]
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    departure_from: datetime
    departure_to: datetime
    passenger_count: int = Field(default=1, ge=1, le=9)
    origin_node_id: str = Field(min_length=1, max_length=80)
    destination_node_id: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_route_and_window(self) -> SeatStatusRefreshRequest:
        if self.origin.strip() == self.destination.strip():
            raise ValueError("origin and destination must differ")
        if self.departure_to <= self.departure_from:
            raise ValueError("departure_to must be after departure_from")
        return self


SeatStatusCooldownCause = Literal[
    "provider_access_restricted",
    "source_unavailable",
]


class SeatStatusSourceStatus(ApiModel):
    """Current in-memory/Redis hold only; it is distinct from worker provider circuits."""

    provider: Literal["korail", "srt"]
    source: Literal["korail_browser", "srt_live"]
    state: Literal["ready", "cooldown"]
    cause: SeatStatusCooldownCause | None = None
    retry_after_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_cooldown_details(self) -> "SeatStatusSourceStatus":
        if self.state == "ready":
            if self.cause is not None or self.retry_after_seconds is not None:
                raise ValueError("ready seat status source cannot expose cooldown details")
            return self
        if self.cause is None or self.retry_after_seconds is None:
            raise ValueError("cooldown seat status source requires cause and retry_after_seconds")
        return self


class WatchCandidateCreate(ApiModel):
    train_number: str = Field(min_length=1, max_length=40)
    departure_at: datetime
    arrival_at: datetime | None = None
    seat_class: SeatClass
    priority: int = Field(ge=1, le=20)
    registration_evidence_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("train_number")
    @classmethod
    def normalize_train_number(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("train_number cannot be blank")
        return normalized

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def require_aware_candidate_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("candidate times must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_candidate_time_range(self) -> "WatchCandidateCreate":
        if self.arrival_at is not None and self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be later than departure_at")
        return self


class WatchCandidateLatestReservationAttemptRead(ApiModel):
    outcome: ReservationOutcome
    confirmation_outcome: (
        Literal[
            "confirmed_payment_required",
            "not_found",
            "auth_required",
            "provider_blocked",
            "inconclusive",
        ]
        | None
    ) = None
    started_at: datetime
    finished_at: datetime | None
    post_deadline_reconciled_at: datetime | None = None
    payment_hold_end_reason: (
        Literal[
            "confirmed_payment_deadline_elapsed",
            "confirmed_payment_hold_no_longer_present",
        ]
        | None
    ) = None
    retryable: bool
    manual_check_required: bool
    retry_condition: (
        Literal[
            "new_availability_episode",
            "provider_account_reverified",
        ]
        | None
    )

    @field_validator(
        "started_at",
        "finished_at",
        "post_deadline_reconciled_at",
        mode="before",
    )
    @classmethod
    def normalize_attempt_timezone(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def validate_attempt_time_range(self) -> Self:
        for name, value in (
            ("started_at", self.started_at),
            ("finished_at", self.finished_at),
            ("post_deadline_reconciled_at", self.post_deadline_reconciled_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must include a timezone")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class WatchCandidateRead(ApiModel):
    id: str
    train_number: str
    departure_at: datetime
    scheduled_departure_at: datetime
    estimated_departure_at: datetime | None
    actual_departure_at: datetime | None
    delay_minutes: int | None = Field(default=None, ge=0)
    operational_status: OperationalStatus
    booking_window_status: BookingWindowStatus
    operational_source: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    operational_observed_at: datetime | None = None
    operational_fresh_until: datetime | None = None
    arrival_at: datetime | None
    seat_class: SeatClass
    priority: int
    state: Literal[
        "active",
        "observed",
        "seat_found",
        "reservation_attempted",
        "payment_required",
        "suppressed_by_priority",
        "expired",
        "failed",
    ]
    suppressed_by_candidate_id: str | None = None
    registration_evidence: TimetableSeatEvidenceRead | None = None
    latest_observation: "WatchCandidateLatestObservationRead | None" = None
    latest_reservation_attempt: WatchCandidateLatestReservationAttemptRead | None = None

    @field_validator(
        "departure_at",
        "scheduled_departure_at",
        "estimated_departure_at",
        "actual_departure_at",
        "arrival_at",
        "operational_observed_at",
        "operational_fresh_until",
        mode="before",
    )
    @classmethod
    def normalize_candidate_timezone(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def validate_operational_provenance(self) -> "WatchCandidateRead":
        provenance = (
            self.operational_source,
            self.operational_observed_at,
            self.operational_fresh_until,
        )
        if any(value is None for value in provenance):
            if any(value is not None for value in provenance):
                raise ValueError("operational provenance must be complete or absent")
            return self
        if self.operational_fresh_until < self.operational_observed_at:
            raise ValueError("operational fresh_until must not precede observed_at")
        return self


class WatchCandidateLatestObservationRead(ApiModel):
    status: SeatObservationStatus
    source: str
    observed_at: datetime
    fresh_until: datetime
    error_category: ObservationErrorCategory | None

    @field_validator("observed_at", "fresh_until", mode="before")
    @classmethod
    def normalize_observation_timezone(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class WatchCreate(ApiModel):
    provider: Provider
    origin: str = Field(min_length=1, max_length=40)
    origin_node_id: str | None = Field(default=None, min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=40)
    destination_node_id: str | None = Field(default=None, min_length=1, max_length=80)
    travel_date: date
    time_from: time
    time_to: time
    seat_class: SeatClass = SeatClass.STANDARD
    passenger_count: int = Field(default=1, ge=1, le=9)
    train_numbers: list[str] = Field(default_factory=list, max_length=20)
    candidates: list[WatchCandidateCreate] = Field(default_factory=list, max_length=20)
    notification_channel_ids: list[str] = Field(default_factory=list, max_length=20)
    mode: str = Field(default="official", pattern="^(official|experimental)$")
    reservation_policy: ReservationPolicy = ReservationPolicy.NOTIFY_ONLY
    seat_observation_mode: SeatObservationMode = SeatObservationMode.BALANCED
    focused_observation_interval_seconds: int = Field(default=25, ge=20, le=30)

    @field_validator("origin_node_id", "destination_node_id")
    @classmethod
    def normalize_node_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("station node IDs cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_route_and_time(self) -> "WatchCreate":
        if self.origin.strip().casefold() == self.destination.strip().casefold():
            raise ValueError("origin and destination must differ")
        if self.time_from >= self.time_to:
            raise ValueError("time_from must be earlier than time_to")
        if self.travel_date < datetime.now(ZoneInfo("Asia/Seoul")).date():
            raise ValueError("travel_date cannot be in the past")
        if (self.origin_node_id is None) != (self.destination_node_id is None):
            raise ValueError("origin_node_id and destination_node_id must be provided together")
        if self.provider in {Provider.KORAIL, Provider.SRT}:
            if self.origin_node_id is None or self.destination_node_id is None:
                raise ValueError("official watches require both station node IDs")
            if self.origin_node_id.strip() == self.destination_node_id.strip():
                raise ValueError("origin and destination node IDs must differ")
        identities = [
            (item.train_number, item.departure_at, item.seat_class) for item in self.candidates
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("candidates must contain unique train departures and seat classes")
        priorities = [item.priority for item in self.candidates]
        if sorted(priorities) != list(range(1, len(priorities) + 1)):
            raise ValueError("candidate priorities must be unique and contiguous from 1")
        if any(item.seat_class != self.seat_class for item in self.candidates):
            raise ValueError("candidate seat_class must match the watch seat_class")
        seoul = ZoneInfo("Asia/Seoul")
        for item in self.candidates:
            local_departure = item.departure_at.astimezone(seoul)
            local_time = local_departure.time().replace(tzinfo=None)
            if local_departure.date() != self.travel_date:
                raise ValueError("candidate departure_at must be on the watch travel_date")
            if not self.time_from <= local_time <= self.time_to:
                raise ValueError("candidate departure_at must be inside the watch time window")
        candidate_train_numbers = {item.train_number for item in self.candidates}
        if self.candidates and candidate_train_numbers != set(self.train_numbers):
            raise ValueError("train_numbers must match candidate train numbers")
        return self


class WatchUpdate(ApiModel):
    time_from: time | None = None
    time_to: time | None = None
    seat_class: SeatClass | None = None
    passenger_count: int | None = Field(default=None, ge=1, le=9)
    train_numbers: list[str] | None = Field(default=None, max_length=20)
    notification_channel_ids: list[str] | None = Field(default=None, max_length=20)
    payment_deadline: datetime | None = None
    reservation_policy: ReservationPolicy | None = None
    seat_observation_mode: SeatObservationMode | None = None
    focused_observation_interval_seconds: int | None = Field(default=None, ge=20, le=30)

    @field_validator("payment_deadline")
    @classmethod
    def require_aware_payment_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("payment_deadline must include a timezone")
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            null_fields = [key for key, item in value.items() if item is None]
            if null_fields:
                raise ValueError(f"explicit null is not allowed: {', '.join(sorted(null_fields))}")
        return value


class WatchRead(ApiModel):
    id: str
    provider: Provider
    origin: str
    origin_node_id: str | None
    destination: str
    destination_node_id: str | None
    travel_date: date
    time_from: time
    time_to: time
    seat_class: SeatClass
    passenger_count: int
    train_numbers: list[str]
    candidates: list[WatchCandidateRead]
    notification_channel_ids: list[str]
    mode: str
    reservation_policy: ReservationPolicy
    seat_observation_mode: SeatObservationMode
    focused_observation_interval_seconds: int
    status: WatchStatus
    next_check_at: datetime | None
    cooldown_until: datetime | None
    payment_deadline: datetime | None
    reservation_attempted: bool
    unchanged_runs: int
    official_booking_url: AnyHttpUrl | None
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime | None = None

    @field_validator("payment_deadline", "last_checked_at", mode="before")
    @classmethod
    def normalize_payment_deadline_timezone(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @field_validator("official_booking_url")
    @classmethod
    def require_https_watch_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("official_booking_url must use HTTPS")
        return value


class HealthResponse(ApiModel):
    status: str
    experimental_rail_enabled: bool


class ErrorPolicyResult(ApiModel):
    status: WatchStatus
    cooldown_seconds: int | None
    requires_manual_resume: bool
    official_handoff_required: bool = False
    reason: str


class EventRead(ApiModel):
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: datetime


class AuthStatus(ApiModel):
    configured: bool
    authenticated: bool
    registration_allowed: bool
    session_expires_at: datetime | None = None

    @field_validator("session_expires_at", mode="before")
    @classmethod
    def normalize_session_expiry(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class UsernamePasswordCredentials(ApiModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().casefold()
        return value


class LoginResult(ApiModel):
    authenticated: bool
    expires_at: datetime
