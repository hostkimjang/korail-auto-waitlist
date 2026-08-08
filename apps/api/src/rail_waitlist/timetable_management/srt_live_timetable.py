from __future__ import annotations

from datetime import UTC, datetime

from pydantic import AnyHttpUrl

from ..domain import Provider, SeatClass
from ..provider_adapters.base import OFFICIAL_BOOKING_URLS
from ..srt_sidecar.contracts import SrtOfficialSeatStatus, SrtTimetableTrain
from .schemas import (
    SeatAvailability,
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)


def map_srt_live_timetable(trains: list[SrtTimetableTrain]) -> list[TimetableItem]:
    """Map strict SRT sidecar rows without inventing missing fare or schedule data."""

    official_url = AnyHttpUrl(OFFICIAL_BOOKING_URLS[Provider.SRT])
    return [
        TimetableItem(
            provider=Provider.SRT,
            train_number=train.train_number,
            train_type=train.train_type,
            origin=train.origin,
            destination=train.destination,
            departure_at=train.departure_at,
            arrival_at=train.arrival_at,
            adult_fare=train.adult_fare,
            timetable_source="official_provider",
            timetable_retrieved_at=train.observed_at,
            availability=SeatAvailability(
                status=train.standard_status,
                source=train.source,
                observed_at=train.observed_at,
            ),
            seat_classes=[
                _seat_class(
                    SeatClass.STANDARD,
                    train.standard_status,
                    train.observed_at,
                    train.source,
                    official_url,
                    fare=train.adult_fare,
                ),
                _seat_class(
                    SeatClass.FIRST,
                    train.first_status,
                    train.observed_at,
                    train.source,
                    official_url,
                ),
            ],
            official_booking_url=official_url,
        )
        for train in trains
    ]


def _seat_class(
    seat_class: SeatClass,
    status: SrtOfficialSeatStatus,
    observed_at: datetime,
    source: str,
    official_url: AnyHttpUrl,
    *,
    fare: int | None = None,
) -> SeatClassAvailability:
    actions: list[SeatAvailabilityAction] = []
    if status == "available":
        actions.extend(
            [
                SeatAvailabilityAction(kind="official_check", url=official_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        )
    elif status == "waitlist_available":
        actions.extend(
            [
                SeatAvailabilityAction(kind="official_waitlist", url=official_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        )
    elif status == "sold_out":
        actions.append(SeatAvailabilityAction(kind="add_to_watch"))
    return SeatClassAvailability(
        seat_class=seat_class,
        status=status,
        provenance=SeatAvailabilityProvenance(
            kind="official_provider",
            source=source,
            observed_at=observed_at.astimezone(UTC),
        ),
        fare=fare,
        actions=actions,
    )
