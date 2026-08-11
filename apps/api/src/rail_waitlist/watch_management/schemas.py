from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from ..domain import (
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
from ..observations.contracts import ObservationErrorCategory
from ..reservations.contracts import ReservationProgressStage, ReservedSeat
from ..schema_base import ApiModel
from ..timetable_management.schemas import TimetableSeatEvidenceRead


class RegistrationEvidenceConflictDetail(ApiModel):
    code: Literal["registration_evidence_conflict"] = "registration_evidence_conflict"
    reason: Literal["expired"]
    message: str = Field(min_length=1, max_length=240)


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
    progress_stages: list[ReservationProgressStage] = Field(default_factory=list)
    reserved_seats: list[ReservedSeat] = Field(default_factory=list, max_length=9)
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
    manual_rearm_available: bool = False
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
        stage_order = {
            "authenticated_session_ready": 0,
            "target_rechecked": 1,
            "seat_selected": 2,
            "reservation_requested": 3,
        }
        for name, value in (
            ("started_at", self.started_at),
            ("finished_at", self.finished_at),
            ("post_deadline_reconciled_at", self.post_deadline_reconciled_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must include a timezone")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        previous = self.started_at
        previous_stage_order = -1
        seen: set[str] = set()
        for progress in self.progress_stages:
            current_stage_order = stage_order[progress.stage]
            if (
                progress.stage in seen
                or current_stage_order <= previous_stage_order
                or progress.occurred_at < previous
            ):
                raise ValueError("progress_stages must be unique and chronological")
            if self.finished_at is not None and progress.occurred_at > self.finished_at:
                raise ValueError("progress_stages cannot occur after finished_at")
            seen.add(progress.stage)
            previous = progress.occurred_at
            previous_stage_order = current_stage_order
        return self


class WatchCandidateRead(ApiModel):
    id: str
    train_number: str
    train_type: str | None = Field(default=None, min_length=1, max_length=40)
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
        if (
            self.operational_fresh_until is not None
            and self.operational_observed_at is not None
            and self.operational_fresh_until < self.operational_observed_at
        ):
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
    observation_execution_state: Literal["idle", "in_progress"] = "idle"
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
