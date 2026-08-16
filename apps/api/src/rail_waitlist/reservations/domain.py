from __future__ import annotations

from dataclasses import dataclass

from ..domain import ReservationOutcome
from .provider_confirmation.contracts import ReservationConfirmationOutcome
from .reconciliation_policy import ReservationReconciliationResolution


@dataclass(frozen=True, slots=True)
class ReservationAttemptResultPolicy:
    retryable: bool
    manual_check_required: bool
    retry_condition: str | None


def reservation_attempt_result_policy(
    outcome: ReservationOutcome,
) -> ReservationAttemptResultPolicy:
    """Project the retry/manual-check contract shared by events and API reads."""
    return ReservationAttemptResultPolicy(
        retryable=outcome is ReservationOutcome.NOT_AVAILABLE,
        # Provider adapters reserve UNKNOWN for every result where a reservation
        # command may have been dispatched. FAILED therefore means the attempt
        # conclusively stopped before dispatch and needs no official booking check.
        manual_check_required=outcome
        in {ReservationOutcome.UNKNOWN, ReservationOutcome.PROVIDER_BLOCKED},
        retry_condition=(
            "new_availability_episode"
            if outcome is ReservationOutcome.NOT_AVAILABLE
            else (
                "provider_account_reverified"
                if outcome
                in {
                    ReservationOutcome.AUTH_REQUIRED,
                    ReservationOutcome.PROVIDER_BLOCKED,
                }
                else None
            )
        ),
    )


def reservation_attempt_manual_check_required(
    outcome: ReservationOutcome,
    *,
    confirmation_outcome: ReservationConfirmationOutcome | None = None,
    reconciliation_resolution: ReservationReconciliationResolution | None = None,
) -> bool:
    """Project whether official booking state still needs a user check.

    Exact paid evidence and a bounded, exact absence resolution close the ambiguity.
    Exhausting inconclusive reads does not: the provider command can still have succeeded.
    """
    return (
        reservation_attempt_result_policy(outcome).manual_check_required
        and confirmation_outcome is not ReservationConfirmationOutcome.CONFIRMED_PAID
        and reconciliation_resolution is not ReservationReconciliationResolution.CONFIRMED_ABSENT
    )
