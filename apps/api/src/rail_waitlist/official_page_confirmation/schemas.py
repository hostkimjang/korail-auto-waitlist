from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..domain import Provider, SeatClass
from ..official_rail_identity import (
    contains_protection_marker,
    normalize_official_train_number,
)
from ..provider_schema_base import ProviderContractModel
from ..schema_base import ApiModel

OFFICIAL_PAGE_CONFIRMATION_SOURCE = "official-page-user-confirmation"

OfficialPageSeatStatus = Literal[
    "available",
    "sold_out",
    "waitlist_available",
    "not_offered",
]


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
