from __future__ import annotations

from datetime import datetime
from typing import Literal
from typing import cast as _cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ..domain import Provider, SeatClass
from ..observations.contracts import SeatObservationRequest, SeatObservationResult
from ..provider_account_management.contracts import ProviderCredentials
from ..reservations.contracts import ReservationRequest, ReservationResult
from ..reservations.provider_confirmation.contracts import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationSeat,
    ReservationConfirmationTarget,
)
from ..timetable_management.schemas import TimetableItem
from .session_contract import SrtSessionActorState

KOREA = ZoneInfo("Asia/Seoul")


class SrtProviderAdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SrtCredentialRequest(SrtProviderAdapterModel):
    login_method: Literal["membership_number", "email", "phone"]
    login_id: SecretStr = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=1, max_length=200)
    credential_version: int = Field(ge=1)

    @classmethod
    def from_credentials(cls, credentials: ProviderCredentials) -> SrtCredentialRequest:
        return cls(
            login_method=credentials.login_method,
            login_id=SecretStr(credentials.login_id),
            password=SecretStr(credentials.password),
            credential_version=credentials.credential_version,
        )

    def to_credentials(self) -> ProviderCredentials:
        return ProviderCredentials(
            login_method=self.login_method,
            login_id=self.login_id.get_secret_value(),
            password=self.password.get_secret_value(),
            credential_version=self.credential_version,
        )

    def wire_payload(self) -> dict[str, str | int]:
        return {
            "login_method": self.login_method,
            "login_id": self.login_id.get_secret_value(),
            "password": self.password.get_secret_value(),
            "credential_version": self.credential_version,
        }


class SrtSessionStatus(SrtProviderAdapterModel):
    state: SrtSessionActorState
    credential_generation: int | None = Field(default=None, ge=1)
    locally_reusable: bool
    created_age_seconds: float | None = Field(default=None, ge=0)
    last_verified_age_seconds: float | None = Field(default=None, ge=0)
    last_used_age_seconds: float | None = Field(default=None, ge=0)
    local_reuse_remaining_seconds: float | None = Field(default=None, ge=0)
    observation_deferred_until: datetime | None = None


class SrtReadOnlyCallRegistrationRequest(SrtProviderAdapterModel):
    call_id: str = Field(min_length=32, max_length=32)
    request_id: str = Field(min_length=32, max_length=32)


class SrtReadOnlyCallRegistrationResult(SrtProviderAdapterModel):
    accepted: bool
    instance_id: str = Field(min_length=32, max_length=32)


class SrtReadOnlyCallStatus(SrtProviderAdapterModel):
    state: Literal["pending", "terminal", "unknown"]
    instance_id: str = Field(min_length=32, max_length=32)


class SrtLoginRequest(SrtProviderAdapterModel):
    operation: Literal["prewarm", "verify"]
    credential: SrtCredentialRequest


class SrtLoginResult(SrtProviderAdapterModel):
    outcome: Literal[
        "authenticated",
        "invalid_identifier",
        "auth_required",
        "provider_blocked",
        "failed",
    ]


class SrtObserveRequest(SrtProviderAdapterModel):
    request: SeatObservationRequest
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def require_exact_route(self) -> SrtObserveRequest:
        if self.origin.strip() != self.request.origin:
            raise ValueError("origin must match the observation request")
        if self.destination.strip() != self.request.destination:
            raise ValueError("destination must match the observation request")
        return self


class SrtObserveResult(SrtProviderAdapterModel):
    observations: list[SeatObservationResult]


class SrtTimetableOverlayRequest(SrtProviderAdapterModel):
    items: list[TimetableItem]
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    departure_from: datetime
    departure_to: datetime
    passenger_count: int = Field(ge=1, le=9)

    @model_validator(mode="after")
    def require_valid_window(self) -> SrtTimetableOverlayRequest:
        if self.origin.strip() == self.destination.strip():
            raise ValueError("origin and destination must differ")
        for value in (self.departure_from, self.departure_to):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timetable overlay times must include a timezone")
        if self.departure_to <= self.departure_from:
            raise ValueError("departure_to must be after departure_from")
        if any(item.provider is not Provider.SRT for item in self.items):
            raise ValueError("timetable overlay accepts only SRT items")
        return self


class SrtTimetableOverlayResult(SrtProviderAdapterModel):
    items: list[TimetableItem]


SrtOfficialSeatStatus = Literal[
    "unknown",
    "available",
    "sold_out",
    "waitlist_available",
    "not_offered",
]


class SrtTimetableSearchRequest(SrtProviderAdapterModel):
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    departure_from: datetime
    departure_to: datetime
    passenger_count: Literal[1] = 1

    @model_validator(mode="after")
    def require_exact_window(self) -> SrtTimetableSearchRequest:
        if self.origin != self.origin.strip() or self.destination != self.destination.strip():
            raise ValueError("timetable search station names must be trimmed")
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        for value in (self.departure_from, self.departure_to):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timetable search times must include a timezone")
        if self.departure_to <= self.departure_from:
            raise ValueError("departure_to must be after departure_from")
        if (
            self.departure_from.astimezone(KOREA).date()
            != self.departure_to.astimezone(KOREA).date()
        ):
            raise ValueError("timetable search must stay within one KST service date")
        return self


class SrtTimetableTrain(SrtProviderAdapterModel):
    provider: Literal[Provider.SRT] = Provider.SRT
    train_number: str = Field(min_length=1, max_length=40, pattern=r"^\d+$")
    train_type: str = Field(min_length=1, max_length=40)
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    departure_at: datetime
    arrival_at: datetime
    standard_status: SrtOfficialSeatStatus
    first_status: SrtOfficialSeatStatus
    observed_at: datetime
    delay_minutes: int | None = Field(default=None, ge=0, le=999)
    adult_fare: int | None = Field(default=None, ge=0)
    source: Literal["srtrain-2.6.7-accountless"] = "srtrain-2.6.7-accountless"

    @model_validator(mode="after")
    def require_exact_official_result(self) -> SrtTimetableTrain:
        if any(
            value != value.strip()
            for value in (self.train_number, self.train_type, self.origin, self.destination)
        ):
            raise ValueError("official timetable text fields must be trimmed")
        if self.origin == self.destination:
            raise ValueError("official timetable route must have distinct stations")
        for value in (self.departure_at, self.arrival_at, self.observed_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("official timetable timestamps must include a timezone")
        if self.arrival_at < self.departure_at:
            raise ValueError("arrival_at must not precede departure_at")
        return self


class SrtTimetableSearchResult(SrtProviderAdapterModel):
    trains: list[SrtTimetableTrain]


class SrtReserveOnceRequest(SrtProviderAdapterModel):
    request: ReservationRequest
    credential: SrtCredentialRequest

    @model_validator(mode="after")
    def require_srt_request(self) -> SrtReserveOnceRequest:
        if self.request.provider is not Provider.SRT:
            raise ValueError("reserve-once accepts only SRT requests")
        return self


class SrtReserveOnceResult(SrtProviderAdapterModel):
    result: ReservationResult


class SrtReservationConfirmationTarget(SrtProviderAdapterModel):
    attempt_id: str = Field(min_length=1, max_length=80)
    candidate_id: str = Field(min_length=1, max_length=80)
    provider: Literal[Provider.SRT]
    train_number: str = Field(min_length=1, max_length=40)
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    departure_at: datetime
    arrival_at: datetime | None = None
    seat_class: Literal[SeatClass.STANDARD, SeatClass.FIRST]
    passenger_count: int = Field(ge=1, le=9)
    credential_version: int = Field(ge=1)
    purpose: ReservationConfirmationPurpose = ReservationConfirmationPurpose.INITIAL
    reserved_seats: tuple[ReservationConfirmationSeat, ...] = ()
    confirmation_correlation_seats: tuple[ReservationConfirmationSeat, ...] = ()

    @model_validator(mode="after")
    def validate_domain_target(self) -> SrtReservationConfirmationTarget:
        self.to_domain()
        return self

    @classmethod
    def from_domain(
        cls,
        target: ReservationConfirmationTarget,
    ) -> SrtReservationConfirmationTarget:
        return cls(
            attempt_id=target.attempt_id,
            candidate_id=target.candidate_id,
            provider=_cast(Literal[Provider.SRT], target.provider),
            train_number=target.train_number,
            origin=target.origin,
            destination=target.destination,
            departure_at=target.departure_at,
            arrival_at=target.arrival_at,
            seat_class=_cast(
                Literal[SeatClass.STANDARD, SeatClass.FIRST],
                target.seat_class,
            ),
            passenger_count=target.passenger_count,
            credential_version=target.credential_version,
            purpose=target.purpose,
            reserved_seats=target.reserved_seats,
            confirmation_correlation_seats=target.confirmation_correlation_seats,
        )

    def to_domain(self) -> ReservationConfirmationTarget:
        return ReservationConfirmationTarget(
            attempt_id=self.attempt_id,
            candidate_id=self.candidate_id,
            provider=self.provider,
            train_number=self.train_number,
            origin=self.origin,
            destination=self.destination,
            departure_at=self.departure_at,
            arrival_at=self.arrival_at,
            seat_class=self.seat_class,
            passenger_count=self.passenger_count,
            credential_version=self.credential_version,
            purpose=self.purpose,
            reserved_seats=self.reserved_seats,
            confirmation_correlation_seats=self.confirmation_correlation_seats,
        )


class SrtConfirmReservationRequest(SrtProviderAdapterModel):
    target: SrtReservationConfirmationTarget
    credential: SrtCredentialRequest


class SrtReservationConfirmationResult(SrtProviderAdapterModel):
    provider: Literal[Provider.SRT]
    outcome: ReservationConfirmationOutcome
    diagnostic_code: ReservationConfirmationDiagnosticCode | None = None
    source: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    observed_at: datetime
    payment_deadline: datetime | None = None
    official_handoff_url: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_domain_result(self) -> SrtReservationConfirmationResult:
        if (self.outcome is ReservationConfirmationOutcome.INCONCLUSIVE) != (
            self.diagnostic_code is not None
        ):
            raise ValueError("only inconclusive confirmation requires a diagnostic code")
        self.to_domain()
        return self

    @classmethod
    def from_domain(
        cls,
        result: ReservationConfirmationResult,
    ) -> SrtReservationConfirmationResult:
        return cls(
            provider=_cast(Literal[Provider.SRT], result.provider),
            outcome=result.outcome,
            diagnostic_code=result.diagnostic_code,
            source=result.source,
            observed_at=result.observed_at,
            payment_deadline=result.payment_deadline,
            official_handoff_url=result.official_handoff_url,
        )

    def to_domain(self) -> ReservationConfirmationResult:
        return ReservationConfirmationResult(
            provider=self.provider,
            outcome=self.outcome,
            diagnostic_code=self.diagnostic_code,
            source=self.source,
            observed_at=self.observed_at,
            payment_deadline=self.payment_deadline,
            official_handoff_url=self.official_handoff_url,
        )


class SrtConfirmReservationResult(SrtProviderAdapterModel):
    result: SrtReservationConfirmationResult
