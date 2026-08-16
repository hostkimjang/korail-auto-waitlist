"""Provider-neutral fail-closed rules for ambiguous reservation follow-up reads."""

from __future__ import annotations

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
