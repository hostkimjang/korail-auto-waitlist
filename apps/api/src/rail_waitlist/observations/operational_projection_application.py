from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from ..domain import BookingWindowStatus, OperationalStatus, SeatObservationStatus
from .contracts import SeatObservationResult

_BOOKING_OPEN_OBSERVATIONS = frozenset(
    {
        SeatObservationStatus.AVAILABLE,
        SeatObservationStatus.LIMITED,
        SeatObservationStatus.STANDING_PLUS_SEAT,
        SeatObservationStatus.STANDING_ONLY,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.RESERVATION_COMPLETED,
    }
)


class OperationalProjectionCandidate(Protocol):
    scheduled_departure_at: datetime
    estimated_departure_at: datetime | None
    delay_minutes: int | None
    actual_departure_at: datetime | None
    operational_status: OperationalStatus
    booking_window_status: BookingWindowStatus
    operational_source: str | None
    operational_observed_at: datetime | None
    operational_fresh_until: datetime | None


def apply_operational_projection(
    candidate: OperationalProjectionCandidate,
    result: SeatObservationResult,
) -> None:
    """Project only provider facts that also prove the live booking/operation state.

    A sold-out response does not mean that ticket sales are closed, so it deliberately
    leaves the booking window unknown. Terminal provider responses, in contrast, are
    strong enough to prevent an already-departed or cancelled train from being polled.
    """

    operational_status: OperationalStatus | None = None
    booking_window_status: BookingWindowStatus | None = None
    if result.delay_minutes is not None:
        operational_status = OperationalStatus.DELAYED
        candidate.delay_minutes = result.delay_minutes
        candidate.estimated_departure_at = candidate.scheduled_departure_at + timedelta(
            minutes=result.delay_minutes
        )
    if result.status in _BOOKING_OPEN_OBSERVATIONS:
        booking_window_status = BookingWindowStatus.OPEN
    elif result.status is SeatObservationStatus.WAITLIST_AVAILABLE:
        booking_window_status = BookingWindowStatus.WAITLIST
    elif result.status is SeatObservationStatus.DEPARTED:
        operational_status = OperationalStatus.DEPARTED_ORIGIN
        booking_window_status = BookingWindowStatus.CLOSED
        candidate.actual_departure_at = result.observed_at
    elif result.status is SeatObservationStatus.OUT_OF_SERVICE:
        operational_status = OperationalStatus.CANCELLED
        booking_window_status = BookingWindowStatus.CLOSED

    if operational_status is None and booking_window_status is None:
        return
    if operational_status is not None:
        candidate.operational_status = operational_status
    if booking_window_status is not None:
        candidate.booking_window_status = booking_window_status
    candidate.operational_source = result.source
    candidate.operational_observed_at = result.observed_at
    candidate.operational_fresh_until = result.fresh_until
