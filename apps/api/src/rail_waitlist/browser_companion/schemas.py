from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from ..official_rail_identity import (
    contains_protection_marker,
    normalize_official_train_number,
)
from ..provider_schema_base import ProviderContractModel
from ..schema_base import ApiModel

KORAIL_BROWSER_COMPANION_SOURCE: Literal["korail-official-browser-companion"] = (
    "korail-official-browser-companion"
)

KorailBrowserSeatStatus = Literal[
    "available",
    "limited",
    "standing_plus_seat",
    "sold_out",
    "waitlist_available",
    "not_offered",
]


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
    def validate_snapshot_identity(self) -> KorailBrowserSnapshotCreate:
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
