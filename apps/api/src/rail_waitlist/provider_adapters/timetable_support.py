from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain import SeatClass
from ..provider_contracts import RouteValidationError
from ..timetable_management.schemas import (
    SeatAvailabilityAction,
    SeatAvailabilityNotObservedReason,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
)


def official_unknown_seat_classes(
    official_booking_url: str,
    reason: SeatAvailabilityNotObservedReason = "public_api_not_available",
) -> list[SeatClassAvailability]:
    return [
        SeatClassAvailability(
            seat_class=seat_class,
            status="unknown",
            provenance=SeatAvailabilityProvenance(
                kind="not_observed",
                reason=reason,
            ),
            actions=[
                SeatAvailabilityAction(kind="official_check", url=official_booking_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ],
        )
        for seat_class in (SeatClass.STANDARD, SeatClass.FIRST)
    ]


def normalize_station_name(value: str) -> str:
    normalized = value.strip().replace(" ", "")
    return normalized[:-1] if normalized.endswith("역") else normalized


def normalize_departure_window(
    departure_from: datetime,
    departure_to: datetime | None,
) -> tuple[datetime, datetime | None]:
    korea = ZoneInfo("Asia/Seoul")

    def localize(value: datetime) -> datetime:
        return value.astimezone(korea) if value.tzinfo else value.replace(tzinfo=korea)

    local_from = localize(departure_from)
    if departure_to is None:
        return local_from, None
    local_to = localize(departure_to)
    if local_to <= local_from:
        raise RouteValidationError("departure_to must be later than departure_from")
    if local_to.date() != local_from.date():
        raise RouteValidationError(
            "departure_to must be on the same Korea service date as departure_from"
        )
    return local_from, local_to
