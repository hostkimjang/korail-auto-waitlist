from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from ..browser_companion.schemas import KORAIL_BROWSER_COMPANION_SOURCE
from ..domain import Provider, SeatClass
from ..official_page_confirmation.schemas import OFFICIAL_PAGE_CONFIRMATION_SOURCE
from ..provider_registry.korail_search_url_policy import validate_korail_general_search_url
from ..provider_registry.official_url_policy import is_official_provider_host
from ..schema_base import ApiModel


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


SeatAvailabilityStatus = Literal[
    "unavailable",
    "unknown",
    "available",
    "limited",
    "standing_plus_seat",
    "standing_only",
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
            "standing_only",
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
    train_type: str = Field(min_length=1, max_length=40)
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

    @field_validator("train_type", mode="before")
    @classmethod
    def normalize_train_type(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("train_type cannot be blank")
        return normalized

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
