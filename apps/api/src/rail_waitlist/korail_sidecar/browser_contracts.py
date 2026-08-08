from __future__ import annotations

from datetime import date, datetime
from datetime import time as clock_time
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..provider_registry.korail_search_url_policy import validate_korail_general_search_url

SeatStatus = Literal[
    "available",
    "limited",
    "standing_plus_seat",
    "sold_out",
    "waitlist_available",
    "not_offered",
]
KorailTrainType = Literal["KTX", "KTX-산천", "KTX-청룡"]
AdapterErrorReason = Literal[
    "provider_access_restricted",
    "rate_limited",
    "source_unavailable",
    "passenger_count_not_supported",
]
ProtectionTrigger = Literal[
    "http_403_main",
    "http_403_subresource",
    "http_403_business",
    "marker_code_8002",
    "marker_code_8003",
    "marker_code_1405",
    "marker_macro_err1",
    "marker_captcha",
    "marker_netfunnel",
    "marker_abnormal_access",
    "marker_unauthorized_tool",
]

SOURCE_NAME: Literal["korail-official-page-browser"] = "korail-official-page-browser"


class AdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrowserSeatSearchRequest(AdapterModel):
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    travel_date: date
    departure_from: clock_time
    departure_to: clock_time
    passenger_count: int = Field(default=1, ge=1, le=9)

    @field_validator("origin", "destination")
    @classmethod
    def normalize_station(cls, value: str) -> str:
        normalized = " ".join(value.split()).removesuffix("역")
        if not normalized:
            raise ValueError("station cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_route_and_window(self) -> "BrowserSeatSearchRequest":
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if self.departure_from >= self.departure_to:
            raise ValueError("departure_from must be earlier than departure_to")
        return self

    def cache_key(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.origin,
            self.destination,
            self.travel_date.isoformat(),
            self.departure_from.isoformat(),
            self.departure_to.isoformat(),
            self.passenger_count,
        )


class BrowserTrainSnapshot(AdapterModel):
    train_number: str = Field(min_length=1, max_length=40)
    train_type: KorailTrainType
    departure_at: datetime
    arrival_at: datetime
    adult_fare: int | None = Field(default=None, ge=0)
    standard: SeatStatus
    first: SeatStatus
    expected_delay_minutes: int | None = Field(default=None, ge=1, le=999)

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def require_aware_schedule(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("schedule datetimes must include a timezone")
        return value

    @model_validator(mode="after")
    def require_arrival_after_departure(self) -> "BrowserTrainSnapshot":
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be later than departure_at")
        return self


class BrowserSeatSearchResult(AdapterModel):
    source: Literal["korail-official-page-browser"] = SOURCE_NAME
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    travel_date: date
    passenger_count: Literal[1]
    observed_at: datetime
    official_search_url: str | None = Field(default=None, max_length=2048)
    # A successful official response can legitimately contain no trains, especially
    # after the final departure of the current service day. Keep that distinct from
    # malformed/protection responses, which are rejected by the transport parser.
    trains: list[BrowserTrainSnapshot] = Field(max_length=100)

    @field_validator("observed_at")
    @classmethod
    def require_aware_observation(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value

    @field_validator("official_search_url")
    @classmethod
    def require_strict_official_search_url(cls, value: str | None) -> str | None:
        return None if value is None else validate_korail_general_search_url(value)


class BrowserAdapterError(RuntimeError):
    def __init__(self, reason: AdapterErrorReason) -> None:
        self.reason = reason
        super().__init__(reason)


class BrowserProtectionDetected(BrowserAdapterError):
    def __init__(
        self,
        trigger: ProtectionTrigger = "marker_abnormal_access",
        stage: str = "unspecified",
    ) -> None:
        self.trigger = trigger
        self.stage = stage
        super().__init__("provider_access_restricted")


class BrowserRateLimited(BrowserAdapterError):
    def __init__(self) -> None:
        super().__init__("rate_limited")


class BrowserSourceUnavailable(BrowserAdapterError):
    def __init__(self, stage: str = "unspecified") -> None:
        self.stage = stage
        super().__init__("source_unavailable")


class BrowserClient(Protocol):
    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult: ...
