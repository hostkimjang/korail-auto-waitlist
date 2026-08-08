from __future__ import annotations

from datetime import UTC, datetime

from ..domain import ReservationOutcome
from ..watch_management.models import ReservationAttempt
from .provider_confirmation.contracts import ReservationConfirmationOutcome


def _utc_instant(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def payment_hold_end_reason(attempt: ReservationAttempt) -> str | None:
    """Return the exact official-read reason an unpaid hold became unusable.

    Some official reservation lists retain an unpaid row briefly after its own deadline.
    An exact row with an already elapsed official deadline is no longer an actionable
    payment handoff, even though it is not yet absent from that list.
    """

    if (
        attempt.outcome is not ReservationOutcome.PAYMENT_REQUIRED
        or attempt.post_deadline_reconciled_at is None
    ):
        return None
    if attempt.confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND:
        return "confirmed_payment_hold_no_longer_present"
    if (
        attempt.confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and attempt.payment_deadline is not None
        and _utc_instant(attempt.payment_deadline)
        <= _utc_instant(attempt.post_deadline_reconciled_at)
    ):
        return "confirmed_payment_deadline_elapsed"
    return None


def is_payment_hold_ended(attempt: ReservationAttempt) -> bool:
    return payment_hold_end_reason(attempt) is not None
