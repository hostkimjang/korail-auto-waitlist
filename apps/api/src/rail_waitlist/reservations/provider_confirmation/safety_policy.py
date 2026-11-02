"""Provider-neutral fail-closed rules for ambiguous reservation follow-up reads."""

from __future__ import annotations

from datetime import UTC, datetime

from ...domain import Provider
from .contracts import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)


def enforce_confirmation_target_safety(
    target: ReservationConfirmationTarget,
    confirmation: ReservationConfirmationResult,
) -> ReservationConfirmationResult:
    """Reject positive UNKNOWN evidence that lacks provider-specific exact correlation."""

    if (
        target.purpose is not ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
        or confirmation.outcome
        not in {
            ReservationConfirmationOutcome.CONFIRMED_PAID,
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        }
    ):
        return confirmation
    has_exact_correlation = len(target.confirmation_correlation_seats) == target.passenger_count
    positive_is_safe = has_exact_correlation and (
        target.provider is Provider.SRT
        or confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    )
    if positive_is_safe:
        return confirmation
    return ReservationConfirmationResult(
        provider=confirmation.provider,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        diagnostic_code=ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT,
        source=confirmation.source,
        observed_at=confirmation.observed_at,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def enforce_recorded_payment_deadline(
    *,
    provider: Provider,
    recorded_payment_deadline: datetime | None,
    confirmation: ReservationConfirmationResult,
) -> ReservationConfirmationResult:
    """Reject a KORAIL hold whose deadline proves it is another reservation.

    The KORAIL unpaid list carries no car or seat, so a payment follow-up can only match
    the journey. A KORAIL hold's payment deadline is fixed when the hold is created, so a
    listed hold with a different (or missing) deadline belongs to another reservation on
    the same journey, such as a newer attempt's hold or one made on the official site.
    Adopting it would attribute one official hold to two attempts.
    """

    if (
        provider is not Provider.KORAIL
        or recorded_payment_deadline is None
        or confirmation.outcome is not ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    ):
        return confirmation
    if confirmation.payment_deadline is not None and _utc(confirmation.payment_deadline) == _utc(
        recorded_payment_deadline
    ):
        return confirmation
    return ReservationConfirmationResult(
        provider=confirmation.provider,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        diagnostic_code=ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS,
        source=confirmation.source,
        observed_at=confirmation.observed_at,
    )
