from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..domain import Provider, SeatClass, SeatObservationStatus
from ..provider_schema_base import ProviderContractModel

ObservationErrorCategory = Literal[
    "timeout",
    "schema_mismatch",
    "provider_unavailable",
    "partial_failure",
    "unknown",
]


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
