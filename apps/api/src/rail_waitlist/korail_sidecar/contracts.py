from __future__ import annotations

from datetime import date, datetime
from datetime import time as clock_time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

KorailReservationSeatClassValue = Literal["general", "special"]
KorailLoginMethodValue = Literal["membership_number", "email", "phone"]
KorailReservationOutcomeValue = Literal[
    "payment_required",
    "auth_required",
    "consent_required",
    "action_required",
    "provider_blocked",
    "unavailable",
    "failed",
]
KorailLoginVerificationOutcomeValue = Literal[
    "authenticated",
    "auth_required",
    "provider_blocked",
    "failed",
]
KorailSessionActorStateValue = Literal[
    "cold",
    "authenticating",
    "ready",
    "stale",
    "auth_required",
    "blocked",
]


class _InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KorailCredentialRequest(_InternalModel):
    login_method: KorailLoginMethodValue = "membership_number"
    login_id: SecretStr = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")


class KorailLoginVerifyRequest(_InternalModel):
    credential: KorailCredentialRequest


class KorailLoginVerifyResult(_InternalModel):
    outcome: KorailLoginVerificationOutcomeValue


class KorailSessionStateResult(_InternalModel):
    state: KorailSessionActorStateValue
    credential_generation: str | None = None
    created_age_seconds: float | None = Field(default=None, ge=0)
    last_verified_age_seconds: float | None = Field(default=None, ge=0)
    last_used_age_seconds: float | None = Field(default=None, ge=0)
    local_reuse_remaining_seconds: float | None = Field(default=None, ge=0)
    locally_reusable: bool


class KorailReservationConfirmationRequest(_InternalModel):
    attempt_id: str = Field(min_length=1, max_length=100)
    candidate_id: str = Field(min_length=1, max_length=100)
    train_number: str = Field(min_length=1, max_length=5, pattern=r"^[0-9]+$")
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    departure_at: datetime
    arrival_at: datetime | None = None
    seat_class: Literal["standard", "first"]
    passenger_count: Literal[1]
    credential_version: int = Field(ge=1)

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def require_aware_departure(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("reservation times must include a timezone")
        return value

    @model_validator(mode="after")
    def require_distinct_route(self) -> "KorailReservationConfirmationRequest":
        if self.origin.strip() == self.destination.strip():
            raise ValueError("origin and destination must differ")
        if self.arrival_at is not None and self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be later than departure_at")
        return self


class KorailReservationConfirmationResult(_InternalModel):
    outcome: Literal[
        "confirmed_payment_required",
        "not_found",
        "auth_required",
        "provider_blocked",
        "inconclusive",
    ]
    source: Literal["korail-same-session-detail", "korail-reservation-list"]
    observed_at: datetime
    payment_deadline: datetime | None = None
    official_handoff_url: str | None = None

    @field_validator("observed_at", "payment_deadline")
    @classmethod
    def require_aware_confirmation_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("confirmation times must include a timezone")
        return value

    @model_validator(mode="after")
    def require_confirmed_handoff_fields(self) -> "KorailReservationConfirmationResult":
        confirmed = self.outcome == "confirmed_payment_required"
        if confirmed != (self.official_handoff_url is not None):
            raise ValueError("only confirmed payment holds may contain a handoff URL")
        if not confirmed and self.payment_deadline is not None:
            raise ValueError("only confirmed payment holds may contain a payment deadline")
        return self


class KorailReserveOnceRequest(_InternalModel):
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    travel_date: date
    train_number: str = Field(min_length=1, max_length=5, pattern=r"^[0-9]+$")
    train_type: str | None = Field(default=None, min_length=1, max_length=40)
    departure_time: clock_time
    arrival_time: clock_time
    seat_class: KorailReservationSeatClassValue
    credential: KorailCredentialRequest

    @field_validator("origin", "destination")
    @classmethod
    def normalize_station(cls, value: str) -> str:
        normalized = " ".join(value.split()).removesuffix("역")
        if not normalized:
            raise ValueError("station cannot be blank")
        return normalized

    @field_validator("train_type")
    @classmethod
    def normalize_train_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("train_type cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_route_and_times(self) -> "KorailReserveOnceRequest":
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if self.departure_time == self.arrival_time:
            raise ValueError("departure_time and arrival_time must differ")
        return self


class KorailReserveOnceResult(_InternalModel):
    outcome: KorailReservationOutcomeValue
    reason: str = Field(min_length=1, max_length=100)
    seat_clicked: bool
    reservation_clicked: bool
    session_ready_at: datetime | None = None
    target_rechecked_at: datetime | None = None
    seat_selected_at: datetime | None = None
    reservation_requested_at: datetime | None = None

    @field_validator(
        "session_ready_at",
        "target_rechecked_at",
        "seat_selected_at",
        "reservation_requested_at",
    )
    @classmethod
    def require_aware_progress_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("reservation progress times must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_progress_evidence(self) -> KorailReserveOnceResult:
        if self.seat_selected_at is not None and not self.seat_clicked:
            raise ValueError("seat_selected_at requires seat_clicked")
        if self.reservation_requested_at is not None and not self.reservation_clicked:
            raise ValueError("reservation_requested_at requires reservation_clicked")
        times = [
            value
            for value in (
                self.session_ready_at,
                self.target_rechecked_at,
                self.seat_selected_at,
                self.reservation_requested_at,
            )
            if value is not None
        ]
        if times != sorted(times):
            raise ValueError("reservation progress times must be chronological")
        return self
