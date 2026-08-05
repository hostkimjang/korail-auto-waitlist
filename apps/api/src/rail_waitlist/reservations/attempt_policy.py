from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain import ReservationOutcome, SeatObservationStatus
from ..reservation_confirmation import ReservationConfirmationOutcome

CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX = "confirmed-absent-retry:"

RESERVATION_RETRY_EDGE_OBSERVATIONS = frozenset(
    {
        SeatObservationStatus.UNAVAILABLE,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.SOLD_OUT,
        SeatObservationStatus.NOT_OFFERED,
        SeatObservationStatus.DEPARTED,
        SeatObservationStatus.OUT_OF_SERVICE,
    }
)


class ConfirmedAbsentRetrySource(Protocol):
    confirmation_outcome: ReservationConfirmationOutcome | None
    confirmation_observed_at: datetime | None
    episode_key: str
    outcome: ReservationOutcome
    payment_deadline: datetime | None
    post_deadline_reconciled_at: datetime | None


def is_confirmed_absent_retry_source(attempt: ConfirmedAbsentRetrySource) -> bool:
    """Return whether exact negative evidence can safely re-arm one attempt.

    Older PAYMENT_REQUIRED rows may predate persisted payment deadlines and the
    post-deadline marker. They would otherwise remain fenced forever even after an
    official reservation-list read proved the hold absent. Keep this compatibility
    path deliberately narrower than the normal expired-hold flow: a missing deadline,
    exact NOT_FOUND confirmation, and a non-retry episode are all required.
    """
    if (
        attempt.confirmation_outcome is not ReservationConfirmationOutcome.NOT_FOUND
        or attempt.confirmation_observed_at is None
        or attempt.episode_key.startswith(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX)
    ):
        return False
    if attempt.outcome is ReservationOutcome.UNKNOWN:
        return True
    return (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and attempt.payment_deadline is None
        and attempt.post_deadline_reconciled_at is None
    )
