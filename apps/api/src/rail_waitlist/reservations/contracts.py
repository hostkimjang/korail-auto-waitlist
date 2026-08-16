from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from ..domain import (
    Provider,
    ReservationOutcome,
    ReservationResultReasonCode,
    reservation_result_reason_code_for_outcome,
)
from ..observations.contracts import SeatObservationRequest
from ..provider_registry.official_url_policy import (
    OFFICIAL_HOST_ROOTS,
    is_official_provider_host,
)
from ..provider_schema_base import ProviderContractModel


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

POST_REQUEST_UNKNOWN_CORRELATION_REASON_CODES = frozenset(
    {
        ReservationResultReasonCode.AUTHENTICATION_REQUIRED,
        ReservationResultReasonCode.PROVIDER_BLOCKED,
        ReservationResultReasonCode.PROVIDER_UNAVAILABLE,
        ReservationResultReasonCode.PROVIDER_RESPONSE_INVALID,
        ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN,
    }
)


class ReservationProgressStage(ProviderContractModel):
    stage: ReservationProgressStageName
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def require_aware_progress_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reservation progress times must include a timezone")
        return value


class ReservedSeat(ProviderContractModel):
    car_number: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9-]+$")
    seat_number: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9-]+$")

    @field_validator("car_number", "seat_number", mode="before")
    @classmethod
    def normalize_identifier(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class ReservationResult(ProviderContractModel):
    """A reserved outcome is a temporary hold, never a completed payment."""

    outcome: ReservationOutcome = Field(
        description=(
            "reserved means a temporary reservation awaiting user payment, not payment completion"
        )
    )
    result_reason_code: ReservationResultReasonCode = (
        ReservationResultReasonCode.RESERVATION_PENDING
    )
    source: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    observed_at: datetime
    credential_version: int | None = Field(default=None, ge=1)
    payment_deadline: datetime | None = None
    official_handoff_url: AnyHttpUrl | None = None
    progress_stages: tuple[ReservationProgressStage, ...] = ()
    reserved_seats: tuple[ReservedSeat, ...] = Field(default=(), max_length=9)
    confirmation_correlation_seats: tuple[ReservedSeat, ...] = Field(default=(), max_length=9)

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
        if "result_reason_code" not in self.model_fields_set:
            self.result_reason_code = reservation_result_reason_code_for_outcome(self.outcome)
        compatible_reason_codes = {
            ReservationOutcome.PENDING: {ReservationResultReasonCode.RESERVATION_PENDING},
            ReservationOutcome.PAYMENT_REQUIRED: {ReservationResultReasonCode.PAYMENT_HOLD_CREATED},
            ReservationOutcome.RESERVED: {ReservationResultReasonCode.PAYMENT_HOLD_CREATED},
            ReservationOutcome.NOT_AVAILABLE: {
                ReservationResultReasonCode.TARGET_NOT_AVAILABLE,
                ReservationResultReasonCode.SEAT_NOT_AVAILABLE,
                ReservationResultReasonCode.RESERVATION_CONTROL_UNAVAILABLE,
                ReservationResultReasonCode.SEAT_SELECTION_LOST,
            },
            ReservationOutcome.AUTH_REQUIRED: {ReservationResultReasonCode.AUTHENTICATION_REQUIRED},
            ReservationOutcome.PROVIDER_BLOCKED: {ReservationResultReasonCode.PROVIDER_BLOCKED},
            ReservationOutcome.FAILED: {
                ReservationResultReasonCode.PROVIDER_UNAVAILABLE,
                ReservationResultReasonCode.PROVIDER_RESPONSE_INVALID,
                ReservationResultReasonCode.RESERVATION_CONTROL_UNAVAILABLE,
                ReservationResultReasonCode.SEAT_SELECTION_LOST,
                ReservationResultReasonCode.RESERVATION_FAILED,
            },
            ReservationOutcome.UNKNOWN: {
                ReservationResultReasonCode.TARGET_AMBIGUOUS,
                ReservationResultReasonCode.AUTHENTICATION_REQUIRED,
                ReservationResultReasonCode.PROVIDER_BLOCKED,
                ReservationResultReasonCode.DELAY_CONSENT_REQUIRED,
                ReservationResultReasonCode.EXISTING_RESERVATION_ACTION_REQUIRED,
                ReservationResultReasonCode.PROVIDER_NOTICE_ACTION_REQUIRED,
                ReservationResultReasonCode.PROVIDER_UNAVAILABLE,
                ReservationResultReasonCode.PROVIDER_RESPONSE_INVALID,
                ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN,
            },
        }
        if self.result_reason_code not in compatible_reason_codes[self.outcome]:
            raise ValueError("result_reason_code is incompatible with reservation outcome")
        stage_names = [progress.stage for progress in self.progress_stages]
        if len(stage_names) != len(set(stage_names)):
            raise ValueError("reservation progress stages must be unique")
        progress_times = [progress.occurred_at for progress in self.progress_stages]
        if progress_times != sorted(progress_times):
            raise ValueError("reservation progress stages must be chronological")
        if any(progress_time > self.observed_at for progress_time in progress_times):
            raise ValueError("reservation progress cannot occur after the result observation")
        seat_keys = [(seat.car_number, seat.seat_number) for seat in self.reserved_seats]
        if len(seat_keys) != len(set(seat_keys)):
            raise ValueError("reserved_seats must contain unique car and seat pairs")
        correlation_seat_keys = [
            (seat.car_number, seat.seat_number) for seat in self.confirmation_correlation_seats
        ]
        if len(correlation_seat_keys) != len(set(correlation_seat_keys)):
            raise ValueError(
                "confirmation_correlation_seats must contain unique car and seat pairs"
            )
        actionable = self.outcome in {"payment_required", "reserved"}
        if actionable and self.official_handoff_url is None:
            raise ValueError(
                f"{self.outcome} is not payment completion and requires an official_handoff_url"
            )
        post_request_unknown_with_exact_seats = (
            self.outcome is ReservationOutcome.UNKNOWN
            and self.result_reason_code in POST_REQUEST_UNKNOWN_CORRELATION_REASON_CODES
            and any(progress.stage == "reservation_requested" for progress in self.progress_stages)
            and bool(self.confirmation_correlation_seats)
        )
        if not actionable and (
            self.payment_deadline is not None
            or self.official_handoff_url is not None
            or self.reserved_seats
        ):
            raise ValueError(
                "only payment_required or reserved can include payment handoff or seat data"
            )
        if self.confirmation_correlation_seats and not post_request_unknown_with_exact_seats:
            raise ValueError("confirmation correlation seats require a post-request unknown result")
        if actionable and self.confirmation_correlation_seats:
            raise ValueError("payment states cannot include uncertain correlation seats")
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
