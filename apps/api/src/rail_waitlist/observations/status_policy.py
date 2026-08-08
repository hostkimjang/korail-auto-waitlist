from __future__ import annotations

from ..domain import SeatObservationStatus

SEAT_FOUND_STATUSES = frozenset(
    {
        SeatObservationStatus.AVAILABLE,
        SeatObservationStatus.LIMITED,
        SeatObservationStatus.STANDING_PLUS_SEAT,
    }
)
ACTIONABLE_SEAT_STATUSES = SEAT_FOUND_STATUSES | {SeatObservationStatus.WAITLIST_AVAILABLE}
