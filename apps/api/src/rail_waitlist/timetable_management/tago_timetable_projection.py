"""Pure projection of validated TAGO timetable rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, tzinfo
from typing import Protocol

from ..domain import Provider
from .schemas import (
    SeatAvailability,
    SeatAvailabilityNotObservedReason,
    SeatClassAvailability,
    TimetableItem,
)


class UnknownSeatClassProjector(Protocol):
    def __call__(
        self,
        official_booking_url: str,
        *,
        reason: SeatAvailabilityNotObservedReason,
    ) -> list[SeatClassAvailability]: ...


def project_tago_timetable_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime | None,
    retrieved_at: datetime,
    official_booking_url: str,
    service_timezone: tzinfo,
    seat_class_projector: UnknownSeatClassProjector,
) -> list[TimetableItem]:
    """Map one validated raw-day snapshot without transport or cache behavior."""

    result: list[TimetableItem] = []
    for row in rows:
        grade = str(row.get("traingradename", ""))
        normalized_grade = grade.upper()
        if provider == Provider.KORAIL and "KTX" not in normalized_grade:
            continue
        if provider == Provider.SRT and "SRT" not in normalized_grade:
            continue
        try:
            departure_at = datetime.strptime(
                str(row["depplandtime"]).zfill(14), "%Y%m%d%H%M%S"
            ).replace(tzinfo=service_timezone)
            arrival_at = datetime.strptime(
                str(row["arrplandtime"]).zfill(14), "%Y%m%d%H%M%S"
            ).replace(tzinfo=service_timezone)
        except (KeyError, ValueError):
            continue
        if departure_at < departure_from:
            continue
        if departure_to is not None and departure_at > departure_to:
            continue
        raw_fare = str(row.get("adultcharge", "")).replace(",", "").strip()
        try:
            adult_fare = int(raw_fare) if raw_fare else None
        except ValueError:
            adult_fare = None
        result.append(
            TimetableItem.model_validate(
                {
                    "provider": provider,
                    "train_number": str(row.get("trainno", "")),
                    "train_type": grade or provider.value,
                    "origin": str(row.get("depplacename", origin)),
                    "destination": str(row.get("arrplacename", destination)),
                    "departure_at": departure_at,
                    "arrival_at": arrival_at,
                    "adult_fare": adult_fare,
                    "timetable_source": "TAGO",
                    "timetable_retrieved_at": retrieved_at,
                    "availability": SeatAvailability(status="unavailable"),
                    "seat_classes": seat_class_projector(
                        official_booking_url,
                        reason="source_not_configured",
                    ),
                    "official_booking_url": official_booking_url,
                }
            )
        )
    return result
